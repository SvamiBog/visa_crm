from app.core.directions import new_cities


def test_a_city_is_added_once_whatever_the_case_and_yo():
    assert new_cities(["Могилёв"], ["могилев", "МИНСК", "Минск", "  Брест  ", ""]) == [
        "МИНСК",
        "Брест",
    ]


def test_cities_keep_the_order_they_were_typed_in():
    assert new_cities([], ["Пинск", "Брест", "Гродно"]) == ["Пинск", "Брест", "Гродно"]


def test_spaces_inside_a_name_are_tidied():
    assert new_cities([], ["Нижний   Новгород"]) == ["Нижний Новгород"]
