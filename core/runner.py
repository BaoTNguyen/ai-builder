"""
Shared agentic loop for all agents in this project.

Each agent supplies its own system prompt, tool schemas, and dispatch
function. The runner drives the tool-use loop until the model signals
end_turn (or an unexpected stop reason), then returns.
"""

import json
from anthropic import Anthropic

client = Anthropic()


def run_agent(
    system_prompt: str,
    tools: list,
    dispatch,
    messages: list,
    label: str = "agent",
) -> None:
    """
    Drive the tool-use loop until the model reaches end_turn.

    Mutates `messages` in place so the caller retains the full conversation
    history if needed. Prints each tool call as it executes.

    Args:
        system_prompt: the agent's system prompt string
        tools:         list of tool schema dicts (Anthropic format)
        dispatch:      callable(name, tool_input) → JSON string
        messages:      list of message dicts; should contain the opening user turn
        label:         short name shown in the "Running…" line
    """
    print(f"Running {label}…\n")

    while True:
        response = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=8096,
            system=system_prompt,
            tools=tools,
            messages=messages,
        )

        messages.append({"role": "assistant", "content": response.content})

        if response.stop_reason == "end_turn":
            print("Done.\n")
            break

        if response.stop_reason != "tool_use":
            print(f"Unexpected stop reason: {response.stop_reason}")
            break

        tool_results = []
        for block in response.content:
            if block.type == "tool_use":
                preview = json.dumps(block.input)[:80]
                print(f"  → {block.name}({preview}{'…' if len(json.dumps(block.input)) > 80 else ''})")
                result = dispatch(block.name, block.input)
                tool_results.append(
                    {"type": "tool_result", "tool_use_id": block.id, "content": result}
                )

        messages.append({"role": "user", "content": tool_results})
