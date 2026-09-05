from issuesmith.cp2_tier import determine_cp2_tier


def test_light_when_all_conditions_met():
    assert determine_cp2_tier(100, 0, True) == "light"


def test_heavy_when_diff_too_large():
    assert determine_cp2_tier(300, 0, True) == "heavy"


def test_heavy_when_unchecked_ac():
    assert determine_cp2_tier(100, 2, True) == "heavy"


def test_heavy_when_p2_not_all_pass():
    assert determine_cp2_tier(100, 0, False) == "heavy"


def test_boundary_199_lines_is_light():
    assert determine_cp2_tier(199, 0, True) == "light"


def test_boundary_200_lines_is_heavy():
    assert determine_cp2_tier(200, 0, True) == "heavy"


def test_all_conditions_fail_is_heavy():
    assert determine_cp2_tier(300, 3, False) == "heavy"
