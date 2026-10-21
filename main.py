import time
import board
import busio
from adafruit_character_lcd.character_lcd_i2c import Character_LCD_I2C

def initialize_display():
    print(f"Board ID: {board.board_id}")
    try:
        i2c = board.I2C()
    except Exception as e:
        print(f"Failed to initialize I2C: {e}")
        print("Hint: If using FT232H, make sure BLINKA_FT232H=1 is set in your environment.")
        raise

    # Diagnostic scan
    while not i2c.try_lock():
        pass
    try:
        addresses = i2c.scan()
        print(f"I2C devices found: {[hex(a) for a in addresses]}")
    finally:
        i2c.unlock()

    target_address = 0x27
    if not addresses:
        print("No I2C devices found.")
    elif target_address not in addresses:
        print(f"Warning: Expected device at {hex(target_address)} not found. Found: {[hex(a) for a in addresses]}")
        # Try to use the first found device if 0x27 is not there
        target_address = addresses[0]
        print(f"Attempting to use device at {hex(target_address)}")

    try:
        # Initialize the LCD with 16 columns and 2 rows
        lcd = Character_LCD_I2C(i2c, 16, 2, address=target_address)
        lcd.clear()
        time.sleep(0.1) # Small delay for stability after clear
        return lcd
    except Exception as e:
        print(f"Error initializing LCD at {hex(target_address)}: {e}")
        if not addresses:
             print("Hint: Check wiring and ensure D1 and D2 are jumpered if using FT232H.")
        raise

def lprint(lcd, message) -> None:
    print(message)
    lcd.clear()
    lcd.message = message

def timed_print(lcd, messages, delay: int=1) -> None:
    for message in messages:
        lprint(lcd, message)
        time.sleep(delay)
    lcd.clear()

def main() -> None:
    try:
        lcd = initialize_display()
        messages = ["Hello World!", "This is", "a test"]
        timed_print(lcd, messages)
    except Exception as e:
        print(f"Main execution failed: {e}")

if __name__ == "__main__":
    main()