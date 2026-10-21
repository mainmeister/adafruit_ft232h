#!/usr/bin/env python3
"""
Drive activity lights on an FT232H USB-GPIO bridge.

Monitors either Linux filesystem activity (via fanotify) or physical block-device
I/O (via /sys/block/<dev>/stat) and flashes one of two LEDs to indicate read
or write activity. Useful as a visual "is this box doing work right now?" indicator.

Usage:
    sudo python3 drivelightsft232h.py [--source fanotify|block] [--mount /] \\
                                      [--device sda] [--read C0] [--write C1]

Modes:
    fanotify  Watch filesystem events (read/write syscalls) on a mount point.
              Sees page-cache reads, page-cache writes, system housekeeping
              (ls, daemons, etc.) — but NOT the physical disk's background work.
              Default mode.
    block     Poll /sys/block/<dev>/stat and flash on read/write sector deltas.
              Sees REAL physical disk I/O (including background writeback), but
              not page-cache-only activity. Use --device to override the
              auto-detected disk.

Hardware: Adafruit FT232H USB-to-GPIO bridge, two LEDs on C0 (read) and C1 (write).
"""
import argparse
import os
import re
import sys
import time
import ctypes
import select

# Set environment variable for FT232H before importing board
os.environ['BLINKA_FT232H'] = '1'

try:
    import board
    import digitalio
except ImportError:
    print("Error: adafruit-blinka not installed. Please install it with 'pip install adafruit-blinka'.")
    sys.exit(1)

# fanotify constants (from <linux/fanotify.h>)
FAN_CLASS_NOTIF = 0x00000000
FAN_CLOEXEC = 0x00000001
FAN_MARK_ADD = 0x00000001
FAN_MARK_MOUNT = 0x00000010

FAN_ACCESS = 0x00000001
FAN_MODIFY = 0x00000002
# Not strictly necessary for FAN_MARK_MOUNT but included for safety
FAN_EVENT_ON_CHILD = 0x08000000

# fanotify_event_metadata structure
class FanotifyEventMetadata(ctypes.Structure):
    _fields_ = [
        ("event_len", ctypes.c_uint32),
        ("vers", ctypes.c_uint8),
        ("reserved", ctypes.c_uint8),
        ("metadata_len", ctypes.c_uint16),
        ("mask", ctypes.c_uint64),
        ("fd", ctypes.c_int32),
        ("pid", ctypes.c_int32),
    ]

# Setup libc calls
libc = ctypes.CDLL("libc.so.6", use_errno=True)

# int fanotify_init(unsigned int flags, unsigned int event_f_flags);
libc.fanotify_init.argtypes = [ctypes.c_uint, ctypes.c_uint]
libc.fanotify_init.restype = ctypes.c_int

# int fanotify_mark(int fanotify_fd, unsigned int flags, uint64_t mask, int dirfd, const char *pathname);
libc.fanotify_mark.argtypes = [ctypes.c_int, ctypes.c_uint, ctypes.c_uint64, ctypes.c_int, ctypes.c_char_p]
libc.fanotify_mark.restype = ctypes.c_int

# ssize_t read(int fd, void *buf, size_t count);
libc.read.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_size_t]
libc.read.restype = ctypes.c_ssize_t


# --- helpers ---

def device_to_disk(dev_path):
    """Map a /dev/X path to its parent disk name in /sys/block/.

    Handles sd*/hd*/vd* (e.g. sda1 -> sda), nvmeXnYpZ (nvme0n1p2 -> nvme0n1),
    mmcblkXpY (mmcblk0p1 -> mmcblk0), and loopXpY.
    """
    name = os.path.basename(dev_path)

    # NVMe: nvme0n1p2 -> nvme0n1
    m = re.match(r'(nvme\d+n\d+)(p\d+)?$', name)
    if m:
        return m.group(1)

    # MMC: mmcblk0p1 -> mmcblk0
    m = re.match(r'(mmcblk\d+)(p\d+)?$', name)
    if m:
        return m.group(1)

    # Loop: loop0p1 -> loop0
    m = re.match(r'(loop\d+)(p\d+)?$', name)
    if m:
        return m.group(1)

    # SD/HD/VD: sda1 -> sda
    return name.rstrip('0123456789') or name


def resolve_block_device(mountpoint):
    """Given a mountpoint (e.g. '/'), return the parent disk name in /sys/block/.

    Walks /proc/mounts to find the source device, then maps it to its parent disk.
    """
    try:
        with open('/proc/mounts') as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2 and parts[1] == mountpoint:
                    source = parts[0]
                    if source.startswith('/dev/'):
                        return device_to_disk(source)
    except OSError as e:
        print(f"Warning: could not read /proc/mounts: {e}")
    return None


# --- modes ---

def run_fanotify(args, read_led, write_led, flash_duration: float=0.05) -> None:
    """fanotify-based event loop: VFS-level filesystem activity."""
    fd = libc.fanotify_init(FAN_CLASS_NOTIF | FAN_CLOEXEC, os.O_RDONLY)
    if fd == -1:
        errno_val = ctypes.get_errno()
        print(f"Error: fanotify_init failed: {os.strerror(errno_val)} (errno {errno_val})")
        sys.exit(1)

    mask = FAN_ACCESS | FAN_MODIFY
    res = libc.fanotify_mark(fd, FAN_MARK_ADD | FAN_MARK_MOUNT, mask, -1, args.mount.encode())
    if res == -1:
        errno_val = ctypes.get_errno()
        print(f"Error: fanotify_mark failed for '{args.mount}': {os.strerror(errno_val)}")
        os.close(fd)
        sys.exit(1)

    print(f"Source: fanotify (filesystem events)")
    print(f"Monitoring mount: '{args.mount}'")
    print(f"Read LED (Pin {args.read}) and Write LED (Pin {args.write}) initialized.")
    print("Press Ctrl+C to stop.")

    last_read_event = 0
    last_write_event = 0
    buf_size = 16384
    buf = ctypes.create_string_buffer(buf_size)

    try:
        while True:
            # Calculate timeout for select based on when LEDs should turn off
            now = time.time()
            timeout = None

            if read_led.value:
                remaining = (last_read_event + flash_duration) - now
                timeout = max(0, remaining)

            if write_led.value:
                remaining = (last_write_event + flash_duration) - now
                if timeout is None or remaining < timeout:
                    timeout = max(0, remaining)

            r, _, _ = select.select([fd], [], [], timeout)

            if r:
                # Read events from fanotify
                n = libc.read(fd, buf, buf_size)
                if n < 0:
                    errno_val = ctypes.get_errno()
                    if errno_val == 4:  # EINTR
                        continue
                    print(f"Error reading fanotify events: {os.strerror(errno_val)}")
                    break

                offset = 0
                while offset + ctypes.sizeof(FanotifyEventMetadata) <= n:
                    metadata = FanotifyEventMetadata.from_buffer(buf, offset)

                    if metadata.mask & FAN_ACCESS:
                        read_led.value = True
                        last_read_event = time.time()

                    if metadata.mask & FAN_MODIFY:
                        write_led.value = True
                        last_write_event = time.time()

                    # In notification mode, fanotify returns an open file descriptor
                    # We must close it to avoid leaking FDs
                    if metadata.fd >= 0:
                        os.close(metadata.fd)

                    if metadata.event_len < ctypes.sizeof(FanotifyEventMetadata):
                        break
                    offset += metadata.event_len
            else:
                # Timeout reached, turn off LEDs if they've been on long enough
                now = time.time()
                if read_led.value and now - last_read_event >= flash_duration:
                    read_led.value = False
                if write_led.value and now - last_write_event >= flash_duration:
                    write_led.value = False
    finally:
        os.close(fd)


def run_block(args, device, read_led, write_led, flash_duration: float=0.05, poll_interval: float=0.02) -> None:
    """block-layer event loop: physical disk I/O from /sys/block/<dev>/stat.

    /sys/block/<dev>/stat fields (per Documentation/admin-guide/iostats.rst):
      0 read I/Os       4 write I/Os       8  in-flight
      1 read merges     5 write merges     9  io time
      2 read sectors    6 write sectors   10 time in queue
      3 read time       7 write time
    We poll fields 2 and 6 (sectors read/written) and flash on delta.
    """
    stat_path = f"/sys/block/{device}/stat"
    if not os.path.exists(stat_path):
        print(f"Error: '{stat_path}' does not exist. Use --device to specify a different block device.")
        sys.exit(1)

    print(f"Source: block ({stat_path})")
    print(f"Monitoring device: '{device}'")
    print(f"Read LED (Pin {args.read}) and Write LED (Pin {args.write}) initialized.")
    print("Press Ctrl+C to stop.")

    READ_SECTORS_IDX = 2
    WRITE_SECTORS_IDX = 6

    def read_stats():
        with open(stat_path) as f:
            return f.read().split()

    last_stats = read_stats()
    last_read_event = 0
    last_write_event = 0

    try:
        while True:
            # Calculate sleep: max of (poll interval, time-until-LED-off) — sleep the shorter one
            now = time.time()
            sleep_duration = poll_interval

            if read_led.value:
                remaining = (last_read_event + flash_duration) - now
                if remaining < sleep_duration:
                    sleep_duration = max(0, remaining)

            if write_led.value:
                remaining = (last_write_event + flash_duration) - now
                if remaining < sleep_duration:
                    sleep_duration = max(0, remaining)

            if sleep_duration > 0:
                time.sleep(sleep_duration)

            try:
                current_stats = read_stats()
            except OSError as e:
                print(f"Error reading {stat_path}: {e}")
                break

            if len(current_stats) < 11:
                continue

            read_sectors = int(current_stats[READ_SECTORS_IDX])
            write_sectors = int(current_stats[WRITE_SECTORS_IDX])
            last_read_sectors = int(last_stats[READ_SECTORS_IDX])
            last_write_sectors = int(last_stats[WRITE_SECTORS_IDX])

            if read_sectors > last_read_sectors:
                read_led.value = True
                last_read_event = time.time()
            if write_sectors > last_write_sectors:
                write_led.value = True
                last_write_event = time.time()

            last_stats = current_stats

            # Turn off LEDs if past flash duration
            now = time.time()
            if read_led.value and now - last_read_event >= flash_duration:
                read_led.value = False
            if write_led.value and now - last_write_event >= flash_duration:
                write_led.value = False
    except KeyboardInterrupt:
        pass


def main() -> None:
    # Root check
    if os.geteuid() != 0:
        print("Error: This script must be run as root to use fanotify. Try: sudo python drivelightsft232h.py")
        sys.exit(1)

    # Command line arguments
    parser = argparse.ArgumentParser(
        description="Drive activity lights using FT232H and either fanotify (filesystem events) or /sys/block/<dev>/stat (physical disk I/O)."
    )
    parser.add_argument("--source", default="fanotify", choices=["fanotify", "block"],
                        help="Activity source: 'fanotify' (filesystem events, default) or 'block' (physical disk I/O).")
    parser.add_argument("--mount", default="/",
                        help="Mount point to monitor (default: '/'). Used directly in fanotify mode; used to auto-detect the block device in block mode.")
    parser.add_argument("--device", default=None,
                        help="Block device to poll in 'block' mode (e.g., 'sda', 'nvme0n1'). Default: auto-detect from --mount.")
    parser.add_argument("--read", default="C0",
                        help="FT232H pin for read activity (default: 'C0')")
    parser.add_argument("--write", default="C1",
                        help="FT232H pin for write activity (default: 'C1')")
    args = parser.parse_args()

    # Get pin IDs from board module
    try:
        read_pin_id = getattr(board, args.read)
        write_pin_id = getattr(board, args.write)
    except AttributeError:
        print(f"Error: Invalid pin name '{args.read}' or '{args.write}'. Available pins on FT232H include C0-C7, D4-D7, etc.")
        sys.exit(1)

    # Initialize LEDs
    try:
        read_led = digitalio.DigitalInOut(read_pin_id)
        read_led.direction = digitalio.Direction.OUTPUT
        read_led.value = False

        write_led = digitalio.DigitalInOut(write_pin_id)
        write_led.direction = digitalio.Direction.OUTPUT
        write_led.value = False
    except Exception as e:
        print(f"Error initializing FT232H GPIO: {e}")
        sys.exit(1)

    try:
        if args.source == "fanotify":
            run_fanotify(args, read_led, write_led)
        else:  # block
            device = args.device or resolve_block_device(args.mount)
            if not device:
                print(f"Error: could not auto-detect block device for mount '{args.mount}'. Use --device to specify one (e.g., 'sda').")
                sys.exit(1)
            run_block(args, device, read_led, write_led)
    except KeyboardInterrupt:
        print("\nStopping activity monitoring...")
    except Exception as e:
        print(f"\nError in event loop: {e}")
    finally:
        # Cleanup
        try:
            read_led.value = False
            write_led.value = False
            read_led.deinit()
            write_led.deinit()
        except Exception:
            pass
        print("Cleanup complete. Goodbye.")


if __name__ == "__main__":
    main()
