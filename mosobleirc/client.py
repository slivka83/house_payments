from __future__ import annotations

API_BASE = "https://lkk.mosobleirc.ru/api"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

LEGACY_LOGIN_PATH = "/tenants-registration/v2/login"
LOGIN_FIRST_FACTOR_PATH = "/clients/auth/login/first-factor"
LOGIN_SECOND_FACTOR_PATH = "/clients/auth/login/second-factor"
FACTOR_CODE_PATH = "/clients/auth/factors/code"

ACCOUNTS_PATHS = (
    "/private/lk/configuration-items",
    "/api/clients/configuration-items",
    "/api/client/information/configuration-items",
)


class MosOblEIRCError(RuntimeError):
    def __init__(self, message: str, *, status: int | None = None, payload: object = None):
        super().__init__(message)
        self.status = status
        self.payload = payload

    @classmethod
    def from_response(cls, response) -> "MosOblEIRCError":
        try:
            payload = response.json()
        except ValueError:
            payload = response.text

        message = f"HTTP {response.status_code}"
        if isinstance(payload, dict):
            message = str(payload.get("message") or payload.get("errorCode") or message)
        elif payload:
            message = str(payload)

        return cls(message, status=response.status_code, payload=payload)


def _items(payload, depth: int = 2) -> list[dict]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict) and depth > 0:
        for key in ("items", "results", "content", "data", "list"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            if isinstance(value, dict):
                found = _items(value, depth - 1)
                if found:
                    return found
    return []


class MosOblEIRCClient:
    def __init__(
        self,
        phone: str | None = None,
        password: str | None = None,
        token: str | None = None,
        *,
        timeout: float = 30.0,
        session=None,
        prompt_code=None,
    ):
        self.phone = phone
        self.password = password
        self.token = token
        self.timeout = timeout
        self._prompt_code = prompt_code
        self._session = session or self._new_session()

    @staticmethod
    def _new_session():
        import requests

        session = requests.Session()
        session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
        return session

    def _request(self, method: str, url: str, *, headers=None, params=None, json_body=None):
        response = self._session.request(
            method,
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=self.timeout,
        )
        if response.status_code == 204:
            return None
        if response.status_code >= 400:
            raise MosOblEIRCError.from_response(response)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def _data_request(
        self,
        method: str,
        path: str,
        *,
        params=None,
        json_body=None,
        auth: bool = True,
        retry: bool = True,
    ):
        if auth and not self.token:
            self.login()

        headers = {"X-Auth-Tenant-Token": self.token} if auth and self.token else {}
        try:
            return self._request(
                method,
                API_BASE + path,
                headers=headers,
                params=params,
                json_body=json_body,
            )
        except MosOblEIRCError as error:
            if auth and retry and error.status == 401:
                self.token = None
                self.login()
                return self._data_request(
                    method,
                    path,
                    params=params,
                    json_body=json_body,
                    auth=auth,
                    retry=False,
                )
            raise

    def _normalized_phone(self) -> str:
        phone = (self.phone or "").strip()
        digits = "".join(ch for ch in phone if ch.isdigit())
        if not digits:
            return phone
        return digits if phone.startswith("+") else f"+{digits}"

    def login(self) -> str:
        if not self.phone or not self.password:
            raise MosOblEIRCError("Не указаны телефон и пароль (--phone/--password или MOSOBLEIRC_PHONE/MOSOBLEIRC_PASSWORD)")

        try:
            token = self._login_with_factors()
        except MosOblEIRCError as error:
            if error.status not in (404, 405, 501):
                raise
            token = None

        if not token:
            token = self._try_legacy_login()
        if not token:
            raise MosOblEIRCError("Не удалось получить токен ЛКК")

        self.token = token
        return token

    def _try_legacy_login(self) -> str | None:
        try:
            payload = self._request(
                "POST",
                API_BASE + LEGACY_LOGIN_PATH,
                json_body={
                    "phone": self._normalized_phone(),
                    "password": self.password,
                    "loginMethod": "PERSONAL_OFFICE",
                },
            )
        except MosOblEIRCError:
            return None
        if isinstance(payload, dict) and payload.get("token"):
            return str(payload["token"])
        return None

    def _login_with_factors(self) -> str | None:
        payload = self._request(
            "POST",
            API_BASE + LOGIN_FIRST_FACTOR_PATH,
            headers={"User-Agent": USER_AGENT},
            json_body={
                "phone": self._normalized_phone(),
                "password": self.password,
                "loginMethod": "PERSONAL_OFFICE",
            },
        )
        if not isinstance(payload, dict):
            return None

        token = payload.get("legacyToken")
        if token:
            return str(token)

        factors = [str(factor) for factor in payload.get("factors") or []]
        access_token = (payload.get("jwtToken") or {}).get("accessToken")
        if not factors or not access_token:
            return None

        factor = self._pick_factor(factors)
        if factor in ("PHONE", "EMAIL"):
            self.send_factor_code(factor, access_token)

        code = self._ask_code(factor)
        second = self._request(
            "POST",
            API_BASE + LOGIN_SECOND_FACTOR_PATH,
            headers={"Authorization": f"Bearer {access_token}"},
            json_body={"factor": factor, "code": code},
        )
        if isinstance(second, dict) and second.get("legacyToken"):
            return str(second["legacyToken"])
        return None

    def send_factor_code(self, factor: str, access_token: str):
        return self._request(
            "POST",
            API_BASE + FACTOR_CODE_PATH,
            headers={"Authorization": f"Bearer {access_token}"},
            json_body={"factor": factor},
        )

    @staticmethod
    def _pick_factor(factors: list[str]) -> str:
        for preferred in ("TOTP", "PHONE", "EMAIL"):
            if preferred in factors:
                return preferred
        return factors[0]

    def _ask_code(self, factor: str) -> str:
        if self._prompt_code is None:
            raise MosOblEIRCError(
                f"Аккаунт защищён вторым фактором ({factor}), нужен код подтверждения"
            )
        code = str(self._prompt_code(factor)).strip()
        if not code:
            raise MosOblEIRCError("Пустой код подтверждения")
        return code

    @staticmethod
    def _parse_accounts(payload) -> list[dict]:
        accounts = []
        for item in _items(payload):
            personal_account = item.get("personalAccount") or {}
            personal_account_id = personal_account.get("id", item.get("personalAccountId"))
            if personal_account_id is None:
                continue
            accounts.append(
                {
                    "id": str(item.get("id")),
                    "name": item.get("name") or personal_account.get("accountNumber") or f"ЛС {personal_account_id}",
                    "personal_account_id": str(personal_account_id),
                    "account_number": personal_account.get("accountNumber"),
                    "raw": item,
                }
            )
        return accounts

    def accounts(self) -> list[dict]:
        last_error: MosOblEIRCError | None = None
        last_shape = "-"
        for path in ACCOUNTS_PATHS:
            try:
                payload = self._data_request("GET", path)
            except MosOblEIRCError as error:
                last_error = error
                if error.status in (400, 401, 403):
                    raise
                continue

            last_shape = list(payload)[:10] if isinstance(payload, dict) else type(payload).__name__
            accounts = self._parse_accounts(payload)
            if accounts:
                return accounts

        if last_error is not None and last_error.status not in (404, 405):
            raise last_error

        raise MosOblEIRCError(
            "ЛКК вернул пустой список лицевых счетов "
            f"(проверены {', '.join(ACCOUNTS_PATHS)}, последний ответ: {last_shape})"
        )

    def charge_details(self, personal_account_id: str, date: str) -> list[dict]:
        payload = self._data_request(
            "GET",
            f"/api/personal_account/charge-details/{personal_account_id}",
            params={"date": date},
        )
        if isinstance(payload, dict):
            return _items(payload.get("chargeDetails") or payload)
        return _items(payload)

    def receipts_by_period(self, personal_account_id: str, date: str, service_type: str | None = None):
        return self._data_request(
            "GET",
            f"/api/personal_account/receipts_by_period/{personal_account_id}",
            params={"date": date, "serviceType": service_type},
        )

    def receipt_pdf(
        self,
        personal_account_id: str,
        date: str,
        service_type: str = "UTILITIES",
        retry: bool = True,
    ) -> bytes:
        if not self.token:
            self.login()

        response = self._session.get(
            API_BASE + f"/api/personal_account/receipts_by_period/{personal_account_id}",
            params={"date": date, "serviceType": service_type},
            headers={"X-Auth-Tenant-Token": self.token, "Accept": "application/pdf"},
            timeout=self.timeout,
        )
        if response.status_code == 401 and retry:
            self.token = None
            self.login()
            return self.receipt_pdf(personal_account_id, date, service_type, retry=False)
        if response.status_code >= 400:
            raise MosOblEIRCError.from_response(response)

        body = response.content
        if not body.startswith(b"%PDF"):
            message = body.decode("utf-8", errors="replace")[:200] or "пустой ответ"
            raise MosOblEIRCError(f"ЛКК не вернул PDF квитанции: {message}")
        return body

    def turnover_statements(
        self,
        personal_account_id: str,
        *,
        year: int | None = None,
        page: int = 0,
        size: int = 100,
        debt_type: str = "UTILITIES",
    ) -> dict:
        params = {"page": page, "size": size, "debtType": debt_type}
        if year is not None:
            params["year"] = year
        payload = self._data_request(
            "GET",
            f"/api/clients/personal-accounts/{personal_account_id}/turnover-statements",
            params=params,
        )
        return payload if isinstance(payload, dict) else {}
