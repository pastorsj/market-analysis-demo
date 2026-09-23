"""Regression coverage for Relay's LangChain multi-block message boundary."""

from langchain_core.messages import (
    AIMessage,
    HumanMessage,
    ToolMessage,
    messages_from_dict,
    messages_to_dict,
)
from langchain_nvidia_switchyard.request_mapper import SwitchyardRequestMapper
from nemo_relay import LLMRequest
from nemo_relay.integrations.langchain._serialization import LangChainCodec


def test_relay_codec_keeps_one_multi_tool_assistant_boundary_for_switchyard() -> None:
    expected_ids = ["call-read-guide", "call-read-shock"]
    tool_calls = [
        {
            "type": "tool_call",
            "name": "read_file",
            "args": {"file_path": "/skills/market-research-guide/SKILL.md"},
            "id": expected_ids[0],
        },
        {
            "type": "tool_call",
            "name": "read_file",
            "args": {"file_path": "/skills/shock-propagation/SKILL.md"},
            "id": expected_ids[1],
        },
    ]
    messages = [
        HumanMessage("Explain the demo and then investigate the shock."),
        AIMessage(
            content=[dict(call) for call in tool_calls],
            tool_calls=[dict(call) for call in tool_calls],
        ),
        ToolMessage("Guide skill", tool_call_id=expected_ids[0]),
        ToolMessage("Shock skill", tool_call_id=expected_ids[1]),
    ]
    original = LLMRequest(
        {},
        {"model": "auto", "messages": messages_to_dict(messages)},
    )

    codec = LangChainCodec()
    round_tripped_request = codec.encode(codec.decode(original), original)
    round_tripped = messages_from_dict(round_tripped_request.content["messages"])

    assistants = [message for message in round_tripped if isinstance(message, AIMessage)]
    assert len(assistants) == 1
    assert [call["id"] for call in assistants[0].tool_calls] == expected_ids
    assert all(message.content or message.tool_calls for message in assistants)
    assert [
        message.tool_call_id
        for message in round_tripped
        if isinstance(message, ToolMessage)
    ] == expected_ids

    mapped = SwitchyardRequestMapper.to_switchyard(
        round_tripped,
        tools=[],
        tool_choice=None,
        model_settings={},
        stop=None,
    )
    assistant_turns = [
        turn for turn in mapped["messages"] if turn["role"] == "assistant"
    ]
    assert len(assistant_turns) == 1
    assert [block["id"] for block in assistant_turns[0]["content"]] == expected_ids
