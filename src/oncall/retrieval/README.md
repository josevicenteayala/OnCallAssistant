# retrieval (read path)

Local, laptop-friendly RAG over the structured cases — runs without any AWS
Knowledge Base so you can validate the ask -> retrieve -> answer loop right after
extraction.

- `embeddings.py`  Titan embeddings (Bedrock invoke_model)
- `store.py`       cosine top-k (pure, unit-tested)
- `index.py`       CLI: embed indexable cases into ./data/index.json
- `answer.py`      CLI: retrieve top-k + grounded, cited answer (one-shot or REPL)

Run:
    make index
    make ask Q="argocd synced a bad manifest and pods are crashlooping"

Migration: at the infra step, swap this local index for a Bedrock Knowledge Base
(RetrieveAndGenerate). The answer prompt in `prompts.py` stays identical — only
the retrieval call changes.
