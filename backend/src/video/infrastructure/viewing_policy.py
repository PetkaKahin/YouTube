"""Immutable Casbin ABAC rules; live permissions arrive as trusted attributes."""

from typing import cast

import casbin  # pyright: ignore[reportMissingTypeStubs] -- upstream has no stubs

from common.contracts.authorization import Authorization
from common.infrastructure.authorization import CasbinAuthorization, PolicyEngine
from video.application.access import ViewingPolicy
from video.domain.access import ViewConditions, Viewer

MATCHER = (
    "r.act == p.act && r.obj.published && r.obj.visible && r.obj.playable && "
    "(r.obj.public || (r.sub.authenticated && "
    "(r.sub.account_id == r.obj.owner_id || r.obj.permitted)))"
)
MODEL = f"""
[request_definition]
r = sub, obj, act
[policy_definition]
p = act
[policy_effect]
e = some(where (p.eft == allow))
[matchers]
m = {MATCHER}
"""


class CasbinViewingPolicy:
    def __init__(self, authorization: Authorization) -> None:
        self.authorization = authorization

    def allows(self, viewer: Viewer, conditions: ViewConditions) -> bool:
        return self.authorization.allows(viewer, conditions, "view")


def create_viewing_policy() -> ViewingPolicy:
    model = casbin.Model()
    model.load_model_from_text(MODEL)  # pyright: ignore[reportUnknownMemberType]
    engine = casbin.Enforcer(model)
    engine.add_policy("view")  # pyright: ignore[reportUnknownMemberType]
    return CasbinViewingPolicy(CasbinAuthorization(cast(PolicyEngine, engine)))
