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

