from __future__ import annotations

import json
import time

from .base import LLM, data_url, shrink


# Fast, frequent calls think less; the few calls that shape the verdict think more.
EFFORT = {"play": "minimal", "look": "minimal", "reflect": "medium", "strategy": "medium", "profile": "medium",
          "repair_hooks": "medium", "judge": "medium"}
# The rare calls that need real reasoning about code and play can go to a stronger model.
REASONING_TASKS = {"profile", "repair_hooks", "strategy", "reflect", "judge"}


class OpenAILLM(LLM):
    name = "openai"

    def __init__(self, model: str, strong_model: str | None = None, image_width=512, image_detail="low", max_tokens=16000):
        super().__init__()
        from openai import OpenAI

        self.client = OpenAI(timeout=120, max_retries=1)  # our own retry loop handles the rest
        self.model = model
        self.strong_model = strong_model or model
        self.image_width = image_width
        self.image_detail = image_detail
        self.max_tokens = max_tokens

    def ask_json(self, task, system, prompt, images=()):
        content = [{"type": "text", "text": prompt}]
        for png in images:
            content.append({
                "type": "image_url",
                "image_url": {"url": data_url(shrink(png, self.image_width)), "detail": self.image_detail},
            })
        messages = [{"role": "system", "content": system}, {"role": "user", "content": content}]

        model = self.strong_model if task in REASONING_TASKS else self.model
        extra = {}
        if model.startswith(("gpt-5", "o3", "o4")):
            extra["reasoning_effort"] = EFFORT.get(task, "low")
        for attempt in range(3):
            try:
                resp = self.client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    max_completion_tokens=self.max_tokens,
                    **extra,
                )
                break
            except Exception as e:  # rate limits and the odd 5xx
                if attempt == 2:
                    raise
                print(f"  [llm] {task}: {e.__class__.__name__}, retrying")
                time.sleep(2 * (attempt + 1))

        u = resp.usage
        self.usage.add(task, u.prompt_tokens if u else 0, u.completion_tokens if u else 0)
        text = resp.choices[0].message.content or "{}"
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            start, end = text.find("{"), text.rfind("}")
            return json.loads(text[start:end + 1]) if start >= 0 else {}
