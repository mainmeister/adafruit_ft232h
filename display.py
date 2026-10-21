import time
import board
import busio
from adafruit_character_lcd.character_lcd_i2c import Character_LCD_I2C
from LCD import LCD

def lprint(lcd, message) -> None:
    print(message)
    lcd.message = message

def main() -> None:
    print(f"Board ID: {board.board_id}")
    try:
        i2c = board.I2C()  # uses board.SCL and board.SDA
    except Exception as e:
        print(f"Failed to initialize I2C: {e}")
        print("Hint: If using FT232H, make sure BLINKA_FT232H=1 is set in your environment.")
        return

    # Diagnostic scan
    while not i2c.try_lock():
        pass
    try:
        addresses = [hex(a) for a in i2c.scan()]
        print(f"I2C devices found: {addresses}")
    finally:
        i2c.unlock()

    if not addresses:
        print("No I2C devices found. Please check your wiring.")
        print("If using FT232H, ensure:")
        print("  1. D0 is SCL, D1 is SDA.")
        print("  2. D1 and D2 are connected together (jumpered) for SDA read.")
        print("  3. The device is powered (VCC and GND connected).")
        return

    target_address = 0x27
    if hex(target_address) not in addresses:
        print(f"Warning: Expected device at {hex(target_address)} not found.")
        if addresses:
            target_address = int(addresses[0], 16)
            print(f"Attempting to use found device at {hex(target_address)} instead.")
        else:
            return

    try:
        lcd = Character_LCD_I2C(i2c, 16, 2, address=target_address)
        lcd.clear()
        lprint(lcd, "Hello World!")
        time.sleep(1)
        lprint(lcd, "This is")
        time.sleep(1)
        lprint(lcd, "a test")
        time.sleep(1)
        lcd.clear()
    except Exception as e:
        print(f"Error initializing LCD: {e}")
        if "No I2C device" in str(e):
            print("The device was found during scan but is now not responding.")
        else:
            print("Hint: This library is for MCP23008-based backpacks. If you have a PCF8574-based backpack (very common), this library may not work correctly.")

if __name__ == "__main__":
    main()