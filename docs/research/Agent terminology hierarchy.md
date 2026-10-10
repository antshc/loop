# AI agent terms

In AI agent systems, Conversation, Session, Turn, Message, Invocation, and Run represent different concepts.

## Agent terminology hierarchy

```text
Conversation — logical dialogue with an agent
└── Session — persistent execution context
    ├── Turn 1
    │   ├── User message: "Implement feature X"
    │   └── Assistant message: "Implemented"
    └── Turn 2
        ├── User message: "Fix the tests"
        └── Assistant message: "Tests fixed"
```

## Terminology

| Term | Meaning |
| --- | --- |
| **Conversation** | Logical sequence of interactions |
| **Session** | Provider-managed context, potentially resumable |
| **Turn** | One interaction cycle |
| **Message** | Individual user, assistant, or tool message |
| **Invocation** | Request to execute the agent |
| **Run** | Actual execution of an invocation |
| **Result** | Final execution outcome |
| **Event** | Intermediate output during a run |

A turn can contain multiple messages, including assistant tool calls and tool results.