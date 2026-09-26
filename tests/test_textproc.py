from talki import textproc as tp


def test_snippet_placeholders_longest_wins_and_expand():
    snips = [tp.SnippetRef(1, "my link", "LINK"), tp.SnippetRef(2, "my link please", "LONG")]
    text, used = tp.insert_snippet_placeholders("Here is My Link please, thanks", snips)
    assert text == "Here is [[S1]], thanks"
    assert tp.expand_placeholders(text, used) == "Here is LONG, thanks"


def test_snippet_missing_placeholder_is_appended():
    snips = [tp.SnippetRef(1, "sig", "— J")]
    text, used = tp.insert_snippet_placeholders("thanks sig", snips)
    assert tp.expand_placeholders("Thanks.", used) == "Thanks. — J"


def test_snippet_needs_word_boundary():
    snips = [tp.SnippetRef(1, "addr", "X")]
    text, used = tp.insert_snippet_placeholders("my address", snips)
    assert not used and text == "my address"


def test_press_enter():
    assert tp.strip_press_enter("send it now, press enter.") == ("send it now", True)
    assert tp.strip_press_enter("press enter to continue") == ("press enter to continue", False)


def test_corrections_skip_contractions():
    text, hits = tp.apply_corrections("can't cant and Kubernetis", [("cant", "Kant"), ("kubernetis", "Kubernetes")])
    assert text == "can't Kant and Kubernetes"
    assert set(hits) == {"Kant", "Kubernetes"}


def test_basic_format():
    assert tp.basic_format("um so hello hello there new line second line", True) == "So hello there\nSecond line."


def test_drop_trailing_period():
    assert tp.drop_trailing_period("Sounds good.") == "Sounds good"
    assert tp.drop_trailing_period("One. Two. Three.") == "One. Two. Three."
    assert tp.drop_trailing_period("Really?") == "Really?"
    assert tp.drop_trailing_period("Wait...") == "Wait..."


def test_join_mid_sentence():
    assert tp.join_with_context("The weather is nice.", "I think") == " the weather is nice"
    assert tp.join_with_context("I agree.", "Well,") == " I agree"
    assert tp.join_with_context("Hello there.", "Done. ") == "Hello there."
    assert tp.join_with_context("Hello.", "") == "Hello."
    assert tp.join_with_context("NASA launched.", "and then") == " NASA launched"


def test_ide_file_tags():
    assert tp.ide_file_tags("look at main dot py please") == "look @main.py please"
    assert tp.ide_file_tags("tag utils.ts and fix it") == "@utils.ts and fix it"
    assert tp.ide_file_tags("meet at noon") == "meet at noon"


def test_search_url():
    assert tp.search_url("hey google what is rust").startswith("https://www.google.com/search?q=what+is+rust")
    assert tp.search_url("ask claude how to cook rice").startswith("https://claude.ai/new?q=how+to+cook+rice")
    assert tp.search_url("make this formal") is None


def test_hallucination_and_count():
    assert tp.is_hallucination(" Thank you. ")
    assert not tp.is_hallucination("Thank you for the file.")
    assert tp.word_count("It's a test, isn't it?") == 5


def test_identifiers():
    ids = tp.extract_identifiers("def load_config(): userName = main.py")
    assert "load_config" in ids and "userName" in ids and "main.py" in ids


def test_strip_fillers():
    assert tp.strip_fillers("Um, hey, can you uh send it") == "Hey, can you send it"
    assert tp.strip_fillers("Ähm, wir treffen uns, äh, morgen") == "Wir treffen uns, morgen"
    assert tp.strip_fillers("Er sagt, ah, das Album ist gut") == "Er sagt, ah, das Album ist gut"
