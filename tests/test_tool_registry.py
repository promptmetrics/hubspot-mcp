def test_tool_decorator_registers():
    from hubspot_mcp.tools import tool, registry

    @tool(name="test_tool", description="A test tool")
    async def test_tool(x: int) -> dict:
        return {"result": x * 2}

    assert "test_tool" in registry
    assert registry["test_tool"].description == "A test tool"
    assert registry["test_tool"].is_async is True


def test_get_tool():
    from hubspot_mcp.tools import get_tool
    td = get_tool("test_tool")
    assert td is not None
    assert td.name == "test_tool"


def test_list_tools():
    from hubspot_mcp.tools import list_tools
    tools = list_tools()
    assert any(t.name == "test_tool" for t in tools)
