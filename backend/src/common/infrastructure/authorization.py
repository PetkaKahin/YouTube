"""Casbin adapter. Business permissions are added with the owning modules."""

from typing import Protocol, cast

import casbin  # pyright: ignore[reportMissingTypeStubs] -- upstream package has no stubs

from common.contracts.authorization import Authorization

MODEL = """
[request_definition]
r = sub, obj, act
[policy_definition]
p = sub, obj, act
[role_definition]
g = _, _
[policy_effect]
e = some(where (p.eft == allow))
[matchers]
m = g(r.sub, p.sub) && r.obj == p.obj && r.act == p.act
"""


class PolicyEngine(Protocol):
    def enforce(self, subject: object, resource: object, action: str) -> bool: ...


class CasbinAuthorization(Authorization):
    def __init__(self, engine: PolicyEngine) -> None:
        self._engine = engine

    def allows(self, subject: object, resource: object, action: str) -> bool:
        return self._engine.enforce(subject, resource, action)


def create_authorization() -> Authorization:
    model = casbin.Model()
    model.load_model_from_text(MODEL)  # pyright: ignore[reportUnknownMemberType]
    return CasbinAuthorization(cast(PolicyEngine, casbin.Enforcer(model)))
