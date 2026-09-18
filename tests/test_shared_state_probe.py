from amplifier_web.shared_state_probe import text_content


def test_shared_state_probe_only_projects_displayable_user_and_assistant_text():
    assert text_content({"content": "plain"}) == "plain"
    assert text_content({"content": [
        {"type": "text", "text": "visible"},
        {"type": "tool_use", "input": {"private": "value"}},
        {"type": "output_text", "text": "also visible"},
    ]}) == "visible\nalso visible"
    assert text_content({"content": {"private": "value"}}) == ""