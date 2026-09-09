"""Cliente configurável do VOXEL PACS para tarefas Non-DICOM."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


class NonDicomCloudError(RuntimeError):
    """Erro técnico de comunicação com o endpoint configurado do VOXEL PACS."""


@dataclass(frozen=True)
class NonDicomCloudConfig:
    base_url: str
    status_path: str
    pending_path: str
    document_path: str
    metadata_path: str
    upload_path: str
    acknowledge_path: str
    status_update_path: str
    manual_claim_path: str
    manual_document_path: str
    manual_status_path: str
    timeout_seconds: int
    tls_enabled: bool
    site_id: str
    router_id: str


class NonDicomCloudClient:
    def __init__(self, config: NonDicomCloudConfig, token: str | None = None) -> None:
        self.config = config
        self.token = token

    @property
    def configured(self) -> bool:
        return bool(self.config.base_url)

    def _url(self, path: str) -> str:
        if not self.configured:
            raise NonDicomCloudError("VOXEL PACS não configurado")
        if self.config.tls_enabled and not self.config.base_url.lower().startswith("https://"):
            raise NonDicomCloudError("TLS está habilitado e exige URL HTTPS")
        return f"{self.config.base_url.rstrip('/')}/{path.lstrip('/')}"

    def _headers(self) -> dict[str, str]:
        headers = {"X-VOXEL-ROUTER-ID": self.config.router_id, "X-VOXEL-SITE-ID": self.config.site_id}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return {key: value for key, value in headers.items() if value}

    async def status(self) -> dict[str, Any]:
        return await self._request("GET", self.config.status_path)

    async def pending(self) -> dict[str, Any]:
        return await self._request("POST", self.config.pending_path, {"site_id": self.config.site_id, "router_id": self.config.router_id})

    async def document(self, document_id: str) -> bytes:
        response = await self._response("GET", self.config.document_path.format(id=document_id))
        return response.content

    async def metadata(self, document_id: str) -> dict[str, Any]:
        return await self._request("GET", self.config.metadata_path.format(id=document_id))

    async def upload(self, metadata: dict[str, Any], content: bytes, file_name: str, mime_type: str) -> dict[str, Any]:
        files = {"file": (file_name, content, mime_type)}
        data = {"metadata": __import__("json").dumps(metadata, ensure_ascii=False)}
        response = await self._response("POST", self.config.upload_path, data=data, files=files)
        return response.json() if response.content else {}

    async def acknowledge(self, submission_id: str, status: str) -> dict[str, Any]:
        return await self._request("POST", self.config.acknowledge_path, {"id": submission_id, "status": status})

    async def update_status(self, submission_id: str, status: str, error: str | None = None) -> dict[str, Any]:
        return await self._request("POST", self.config.status_update_path, {"id": submission_id, "status": status, "error": error})

    async def philips_claim(self) -> dict[str, Any]:
        return await self._request("POST", self.config.pending_path)

    async def philips_document(self, job_id: str, lease_token: str) -> bytes:
        response = await self._response("GET", self.config.document_path.format(id=job_id), headers={**self._headers(), "X-VOXEL-LEASE-TOKEN": lease_token})
        return response.content

    async def philips_status(self, job_id: str, lease_token: str, status: str, reference: str | None = None, error_category: str | None = None) -> dict[str, Any]:
        payload = {"lease_token": lease_token, "status": status}
        if reference:
            payload["reference"] = reference
        if error_category:
            payload["error_category"] = error_category
        return await self._request("POST", self.config.status_update_path.format(id=job_id), payload)

    async def manual_test_claim(self) -> dict[str, Any]:
        return await self._request("POST", self.config.manual_claim_path)

    async def manual_test_document(self, test_id: str, lease_token: str) -> bytes:
        response = await self._response("GET", self.config.manual_document_path.format(id=test_id), headers={**self._headers(), "X-VOXEL-LEASE-TOKEN": lease_token})
        return response.content

    async def manual_test_status(self, test_id: str, lease_token: str, status: str) -> dict[str, Any]:
        return await self._request("POST", self.config.manual_status_path.format(id=test_id), {"lease_token": lease_token, "status": status})

    async def _request(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._response(method, path, json=body)
        return response.json() if response.content else {}

    async def _response(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            request_headers = kwargs.pop("headers", None)
            async with httpx.AsyncClient(timeout=self.config.timeout_seconds, verify=self.config.tls_enabled) as client:
                response = await client.request(method, self._url(path), headers=request_headers or self._headers(), **kwargs)
                response.raise_for_status()
                return response
        except httpx.HTTPError as exc:
            raise NonDicomCloudError("Comunicação com VOXEL PACS indisponível") from exc
