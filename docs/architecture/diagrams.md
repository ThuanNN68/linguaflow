# System diagrams

These Mermaid diagrams summarize the current topology. Keep them synchronized
with `ARCHITECTURE.md`, public contracts, and migrations.

## System overview

```mermaid
flowchart TB
    Browser[Next.js browser client]
    API[FastAPI REST and WebSocket]
    Services[Domain services]
    Agents[Translation and assistant agents]
    DB[(PostgreSQL and pgvector)]
    Redis[(Redis pub/sub)]
    Storage[Durable attachment storage]
    Providers[LLM, STT, email, RTC providers]
    Collector[OTLP collector and tracing backend]

    Browser -->|HTTPS / WSS| API
    API --> Services
    Services --> Agents
    Services --> DB
    API <--> Redis
    Services --> Storage
    Agents --> Providers
    Services --> Providers
    API -. request and SQL spans .-> Collector
    Services -. outbound HTTP spans .-> Collector
```

## Message and translation flow

```mermaid
sequenceDiagram
    participant C as Client
    participant W as WebSocket API
    participant M as Messaging service
    participant D as PostgreSQL
    participant T as Translation service
    participant R as Redis backplane

    C->>W: authenticated message event
    W->>M: validate membership and payload
    M->>D: persist original message
    D-->>M: committed message ID
    M-->>W: durable message DTO
    W->>R: publish when configured
    W-->>C: acknowledgement
    M->>T: schedule recipient translations
    T->>D: persist translation version
    T-->>C: translation status/result event
```

## Voice lifecycle

```mermaid
stateDiagram-v2
    [*] --> Uploaded
    Uploaded --> Pending: create voice message
    Pending --> Processing: worker claims operation
    Processing --> Completed: transcript persisted
    Processing --> Failed: retry budget exhausted
    Pending --> Processing: startup recovery
    Failed --> Pending: explicit retry
    Completed --> [*]
```

## Assistant proposal flow

```mermaid
flowchart LR
    Request[Authorized user request] --> Consent{Consent granted?}
    Consent -- no --> Reject[Return consent requirement]
    Consent -- yes --> Retrieve[Retrieve permitted context]
    Retrieve --> Model[Guarded assistant workflow]
    Model --> Validate[Validate structured output]
    Validate --> Proposal[(Persist proposal)]
    Proposal --> Review{User review}
    Review -- approve --> Apply[Apply internal action]
    Review -- reject --> Dismiss[Record dismissal]
```

## Realtime scaling

```mermaid
flowchart TB
    C1[Client A] --> W1[Backend worker 1]
    C2[Client B] --> W2[Backend worker 2]
    W1 <--> R[(Redis pub/sub)]
    W2 <--> R
    W1 --> DB[(PostgreSQL)]
    W2 --> DB
```

Without Redis, only clients connected to the same process receive process-local
broadcasts. Production must therefore use Redis before adding workers or replicas.

## Deployment topology

```mermaid
flowchart TB
    Internet --> TLS[DNS, TLS, reverse proxy]
    TLS --> Frontend[Next.js frontend]
    TLS --> Backend[FastAPI backend]
    Backend --> Database[(Managed PostgreSQL + pgvector)]
    Backend --> Cache[(Redis)]
    Backend --> Objects[Object storage]
    Backend --> External[AI, STT, email, RTC]
```
