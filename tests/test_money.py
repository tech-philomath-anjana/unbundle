import pytest
from unbundle.money import apply_rate, parse_amount, format_amount, within_tolerance    

def test_apply_rate_refuses_negative():
    with pytest.raises(ValueError):
        apply_rate(-725, 200)

# 725 paise at 200 bps is 14.5 paise, a tie, so this case demonstrates the half up rounding 
def test_apply_rate_rounds_half_up():
    assert apply_rate(725, 200) == 15

def test_apply_rate_rounds_down_below_half():
    assert apply_rate(725, 199) == 14

def test_apply_rate_no_rounding():
    assert apply_rate(1450, 200) == 29

def test_apply_rate_zero_rate():
    assert apply_rate(725, 0) == 0

def test_apply_rate_zero_amount():
    assert apply_rate(0, 200) == 0

def test_apply_rate_gst_rate():
    assert apply_rate(725, 1800) == 131

def test_parse_amount_strip_commas_and_whitespace():
    assert parse_amount("   12,345.67   ") == 1234567

def test_parse_amount_refuses_negative():
    with pytest.raises(ValueError):
        parse_amount("-12,345.67")

def test_parse_amount_refuses_more_than_two_decimal_places():
    with pytest.raises(ValueError):
        parse_amount("12,345.678")

def test_parse_amount_refuses_more_than_one_decimal_point():
    with pytest.raises(ValueError):
        parse_amount("12.34.56")

def test_parse_amount_no_decimal_point():
    assert parse_amount("12345") == 1234500

def test_parse_amount_with_two_decimal_places():
    assert parse_amount("12,345.67") == 1234567

def test_parse_amount_no_rupee_paise_only():
    assert parse_amount("0.12") == 12

def test_parse_amount_pads_single_decimal_place_right():
    assert parse_amount("12.3") == 1230

def test_parse_amount_refuses_non_numeric_characters():
    with pytest.raises(ValueError):
        parse_amount("abc")

    with pytest.raises(ValueError):
        parse_amount("12,34a.56")

    with pytest.raises(ValueError):
        parse_amount("")

def test_parse_amount_zero():
    assert parse_amount("0") == 0

# 0.-5 comes back as -5 paise, and a negative amount is a value the rest of the project treats as impossible
def test_parse_amount_refuses_a_sign_after_the_decimal_point():
    with pytest.raises(ValueError):
        parse_amount("0.-5")

    # 1.-5 comes back as 95 paise, which raises nothing and reads as an ordinary amount
    with pytest.raises(ValueError):
        parse_amount("1.-5")

# format_amount never writes a sign, so a cell carrying one came from somewhere else and +5 reading
# as five rupees hides that
def test_parse_amount_refuses_a_leading_sign():
    with pytest.raises(ValueError):
        parse_amount("+5")

# 1_0 comes back as ten rupees, so a cell nobody could have written on purpose is read as a number
# instead of being refused
def test_parse_amount_refuses_underscores_between_digits():
    with pytest.raises(ValueError):
        parse_amount("1_0")

# isdigit alone passes Arabic numerals so the guard is isascii and isdigit, and without both of them
# ١٢ comes back as twelve rupees
def test_parse_amount_refuses_non_ascii_digits():
    with pytest.raises(ValueError):
        parse_amount("١٢")

# Every CSV amount comes out of format_amount and it always writes both halves, so a missing half
# means the cell came from somewhere else and picking which half it is invents a number
def test_parse_amount_refuses_a_missing_half():
    with pytest.raises(ValueError):
        parse_amount(".50")

    with pytest.raises(ValueError):
        parse_amount("5.")

# A blank cell reaches int and raises invalid literal for int, so the message names Python's problem
# and not the column the merchant has to go and fix
def test_parse_amount_refuses_blank_with_its_own_message():
    with pytest.raises(ValueError, match="Amount must be digits"):
        parse_amount("")

    with pytest.raises(ValueError, match="Amount must be digits"):
        parse_amount("   ")

def test_format_amount_refuses_negative():
    with pytest.raises(ValueError):
        format_amount(-12345)

def test_format_amount_rupees_and_paise():
    assert format_amount(1234567) == "12345.67"

def test_format_amount_paise_ending_in_zero():
    assert format_amount(1234560) == "12345.60"

def test_format_amount_single_digit_paise():
    assert format_amount(5) == "0.05"

def test_format_amount_no_paise_only_rupees():
    assert format_amount(1234500) == "12345.00"

def test_format_amount_zero():
    assert format_amount(0) == "0.00"

def test_within_tolerance_within_range():
    assert within_tolerance(1234, 1235, 20) == True

def test_within_tolerance_outside_range():
    assert within_tolerance(1234, 1250, 1) == False

def test_within_tolerance_exactly_on_tolerance():
    assert within_tolerance(1234, 1235, 1) == True

def test_within_tolerance_reversed_arguments():
    assert within_tolerance(1235, 1234, 1) == True

def test_within_tolerance_one_past_tolerance():
    assert within_tolerance(1234, 1236, 1) == False   

def test_within_tolerance_zero_tolerance():
    assert within_tolerance(1234, 1234, 0) == True
    assert within_tolerance(1234, 1235, 0) == False

def test_parse_and_format_are_inverses():
    assert all(parse_amount(format_amount(p)) == p for p in range(100000))

