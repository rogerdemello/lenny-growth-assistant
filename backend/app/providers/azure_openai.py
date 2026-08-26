"""Azure OpenAI.

Same wire format as OpenAI, different URL layout: the model is a *deployment*
in the path and the API version is a query parameter, and the key travels in
`api-key` rather than `Authorization`. Those three differences are the whole
subclass.
"""

from __future__ import annotations

from typing import Any

from app.providers.openai_compat import OpenAICompatProvider


class AzureOpenAIProvider(OpenAICompatProvider):
    def __init__(
        self,
        *,
        endpoint: str,
        api_key: str,
        chat_deployment: str,
        embed_deployment: str = "",
        api_version: str = "2024-10-21",
        timeout: float = 180.0,
    ) -> None:
        super().__init__(
            name="azure",
            base_url=endpoint.rstrip("/"),
            api_key=api_key,
            model=chat_deployment,
            embed_model=embed_deployment or chat_deployment,
            timeout=timeout,
            requires_key=True,
        )
        self.api_version = api_version
        self.embed_deployment = embed_deployment
        self.info.configured = bool(endpoint and api_key and chat_deployment)

    def _chat_url(self) -> str:
        return (
            f"{self.base_url}/openai/deployments/{self.model}"
            f"/chat/completions?api-version={self.api_version}"
        )

    def _embed_url(self) -> str:
        return (
            f"{self.base_url}/openai/deployments/{self.embed_model}"
            f"/embeddings?api-version={self.api_version}"
        )

    def _headers(self) -> dict[str, str]:
        return {"Content-Type": "application/json", "api-key": self.api_key}

    def _chat_body(self, payload: dict[str, Any]) -> dict[str, Any]:
        # The deployment already identifies the model; sending `model` too is
        # accepted but redundant, and some API versions reject a mismatch.
        payload.pop("model", None)
        return payload

    def _embed_body(self, payload: dict[str, Any]) -> dict[str, Any]:
        payload.pop("model", None)
        return payload

    async def ping(self) -> dict[str, Any]:
        if not self.info.configured:
            return {"ok": False, "reason": "not configured"}
        import httpx

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{self.base_url}/openai/models?api-version={self.api_version}",
                    headers=self._headers(),
                )
            return {"ok": resp.status_code < 400, "status": resp.status_code}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": str(exc)[:200]}

    async def embed(self, texts):  # noqa: ANN001, ANN201
        if not self.embed_deployment:
            from app.core.errors import MissingCredentialsError

            raise MissingCredentialsError(
                "Azure OpenAI has no embedding deployment configured.",
                hint="Set AZURE_OPENAI_EMBED_DEPLOYMENT, or leave EMBED_PROVIDER=ollama to embed locally.",
            )
        return await super().embed(texts)
