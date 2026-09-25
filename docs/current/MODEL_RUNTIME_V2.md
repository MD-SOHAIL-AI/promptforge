# ForgeX Model Runtime V2

ForgeX uses deterministic model execution. A request resolves exactly one `provider_id` and `model_id`; automatic cross-provider and cross-model fallback is disabled. Provider identity is separate from protocol identity, model capabilities are validated before execution, and bounded retries may only retry the same selected model. Rate-limit metadata such as `Retry-After` is normalized so Agent sessions can pause and resume without changing providers.
