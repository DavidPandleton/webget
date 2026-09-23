from webget.extraction import extract_structured


def test_extracts_json_ld_and_table():
    html = """
    <script type="application/ld+json">
    {"@type":"Article","headline":"Hello"}
    </script>
    <table><tr><th>Name</th><th>Score</th></tr>
      <tr><td> Ada </td><td> 10 </td></tr>
    </table>
    """
    result = extract_structured(html)
    assert result.status == "success"
    assert result.data["json_ld"][0]["headline"] == "Hello"
    assert result.data["tables"][0]["rows"] == [{"Name": "Ada", "Score": "10"}]


def test_invalid_json_ld_is_incomplete_when_table_survives():
    result = extract_structured(
        '<script type="application/ld+json">{bad</script>'
        '<table><tr><th>Key</th></tr><tr><td>Value</td></tr></table>'
    )
    assert result.status == "incomplete"
    assert result.data["tables"]
    assert "invalid JSON" in result.errors[0]


def test_empty_or_unstructured_input_has_explicit_state():
    assert extract_structured("").status == "error"
    result = extract_structured("<html><p>plain text</p></html>")
    assert result.status == "incomplete"
    assert result.data == {"json_ld": [], "tables": []}
