"""HTTP translation for registration and cookie authentication."""

from datetime import datetime

from dishka import FromDishka
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field

from channel.public import Register, Registered, Registration, RegistrationError
from common.http.errors import problem
from common.http.schemas import FieldError, Identifier, JSONModel
from common.http.sessions import end_session, rotate_session
from user.public import Account, AccountNotFound, Authenticate, GetAccount, InvalidCredentials


class RegisterBody(JSONModel):
    name: str
    email: str
    tag: str
    password: str = Field(repr=False)
    password_confirmation: str = Field(repr=False)


class LoginBody(JSONModel):
    email: str
    password: str = Field(repr=False)


class RegisteredBody(JSONModel):
    account_id: Identifier
    channel_id: Identifier
    tag: str
    created_at: datetime

    @classmethod
    def from_result(cls, value: Registered) -> RegisteredBody:
        return cls(
            account_id=value.account_id,
            channel_id=value.channel_id,
            tag=value.tag,
            created_at=value.created_at,
        )


class AccountBody(JSONModel):
    id: Identifier
    name: str
    email: str
    role: str
    created_at: datetime

    @classmethod
    def from_account(cls, value: Account) -> AccountBody:
        return cls(
            id=value.id,
            name=value.name,
            email=value.email,
            role=value.role.value,
            created_at=value.created_at,
        )


def accounts_router() -> APIRouter:
    router = APIRouter(prefix="/auth", tags=["Accounts"], route_class=DishkaRoute)

    @router.post("/register", status_code=201, response_model=RegisteredBody)
    async def register(
        body: RegisterBody, request: Request, scenario: FromDishka[Register]
    ) -> RegisteredBody | JSONResponse:
        try:
            result = await scenario(Registration(**body.model_dump()))
        except RegistrationError as error:
            return problem(
                request,
                409 if error.code == "already_taken" else 422,
                "registration_failed",
                "Registration failed",
                [FieldError(location=["body", error.field], code=error.code)],
            )
        return RegisteredBody.from_result(result)

    @router.post("/login", response_model=AccountBody)
    async def login(
        body: LoginBody, request: Request, scenario: FromDishka[Authenticate]
    ) -> AccountBody | JSONResponse:
        try:
            account = await scenario(body.email, body.password)
        except InvalidCredentials:
            return problem(request, 401, "invalid_credentials", "Invalid email or password")
        await rotate_session(request)
        request.session.clear()
        request.session["user_id"] = account.id
        return AccountBody.from_account(account)

    @router.get(
        "/me", response_model=AccountBody, openapi_extra={"security": [{"SessionCookie": []}]}
    )
    async def me(request: Request, scenario: FromDishka[GetAccount]) -> AccountBody | JSONResponse:
        account_id = request.session.get("user_id")
        if type(account_id) is not int or not 0 < account_id <= 9223372036854775807:
            return problem(request, 401, "unauthenticated", "Authentication required")
        try:
            account = await scenario(account_id)
        except AccountNotFound:
            return problem(request, 401, "unauthenticated", "Authentication required")
        return AccountBody.from_account(account)

    @router.post("/logout", status_code=204)
    async def logout(request: Request) -> Response:
        await end_session(request)
        return Response(status_code=204)

    return router
