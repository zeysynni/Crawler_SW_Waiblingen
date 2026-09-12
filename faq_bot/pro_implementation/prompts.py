"""The prompt that is meant to be edited.

This file holds the **answering** prompt and nothing else. It decides the bot's
tone, what it does when the retrieved context does not contain the answer, and
which company it says it represents. Edit it freely: it changes answers
immediately, with no re-ingest and no code change.

The other four prompts in this project stay where they are used, on purpose:

    rewrite  -> answer.py:rewrite_query      reply is used as a search query
    rerank   -> answer.py:rerank             reply is parsed into RankOrder
    chunking -> ingest.py:make_prompt        reply is parsed into Chunks
    judge    -> evaluation/eval.py           reply is parsed into AnswerEval

Those four are machinery rather than phrasing: their replies are parsed into
objects, so rewording them can break the parsing, not merely change the output.
See faq_bot/README.md §2.6 and §8.1.

Two rules when editing the string below:

1. Keep the `{context}` placeholder. It is where the retrieved chunks are
   inserted; without it the model gets no knowledge base at all.
2. Any *literal* brace must be doubled — `{{` and `}}` — because this string is
   passed to `str.format()`. A single `{` that is not a placeholder raises
   KeyError at answer time.
"""

SYSTEM_PROMPT = """
You are a knowledgeable, friendly assistant representing the company SW Waiblingen.
You are chatting with a user about SW Waiblingen.
Your answer will be evaluated for accuracy, relevance and completeness, so make sure it only answers the question and fully answers it.
Read the context carefully and look for an answer from the context.If you still don't know the answer after reading the context, say so.
For context, here are specific extracts from the Knowledge Base that might be directly relevant to the user's question:
{context}

With this context, please answer the user's question. Be accurate, relevant and complete.
"""
