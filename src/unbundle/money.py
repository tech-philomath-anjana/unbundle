Paise = int
Bps = int

# int takes a leading sign, underscores between digits and non ASCII digits, so -5 after the dot comes back
# negative, 1_0 comes back as ten and an Arabic numeral comes back as a number, none of which is an amount and
# none of which raises, so every part is checked here rather than letting int decide what an amount is
def _digits_only(part: str) -> bool:
    return part.isascii() and part.isdigit()

def parse_amount(amount_str: str) -> Paise:

    cleaned_amount = amount_str.strip().replace(",", "")

    if cleaned_amount.startswith("-"):
        raise ValueError(f"Amount cannot be negative, got {amount_str}")

    split_decimal_part = cleaned_amount.split('.')

    if len(split_decimal_part) > 2:
        raise ValueError(f"Amount cannot have more than one decimal point, got {amount_str}")

    if len(split_decimal_part) == 1:
        if not _digits_only(split_decimal_part[0]):
            raise ValueError(f"Amount must be digits, got {amount_str}")
        return int(split_decimal_part[0]) * 100

    if len(split_decimal_part[1]) > 2:
        raise ValueError(f"Amount cannot have more than two decimal places, got {amount_str}")
    # Both halves, since the negative guard above reads the whole string and so never sees a sign after the dot
    if not _digits_only(split_decimal_part[0]) or not _digits_only(split_decimal_part[1]):
        raise ValueError(f"Amount must be digits, got {amount_str}")
    # The digit right after the dot is tenths place so 2 means 20,
    # using ljust to make sure it becomes 20 and not 2
    decimal_part = int(split_decimal_part[1].ljust(2, '0'))
    integer_part = int(split_decimal_part[0])

    amount = integer_part * 100 + decimal_part
    return amount

def apply_rate(amount: Paise, rate: Bps) -> Paise:
    # Expression can handle negatives but it rounds half up moving the value towards zero 
    # but money rounds half away from zero, the two conventions contradict each other below 0
    if amount < 0: 
        raise ValueError(f"Amount cannot be negative, got {amount}")
    # Floor division rounds down, adding 5000 which is 0.5 ( 5000 / 10000) to round to nearest integer 
    return (amount * rate + 5000) // 10000 

def format_amount(paise: Paise) -> str:

    if paise < 0:
        raise ValueError(f"Amount cannot be negative, got {paise}")
    
    rupees = paise // 100
    remaining_paise = paise % 100

    remaining_paise_str = str(remaining_paise).rjust(2, '0')

    return f"{rupees}.{remaining_paise_str}"

def within_tolerance(amount1: Paise, amount2: Paise, tolerance: Paise) -> bool:
    # Tolerance is the max drift rounding can cause so a gap exactly equal to tolerance is still within tolerance
    return abs(amount1 - amount2) <= tolerance 

