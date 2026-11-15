"""Subscriptions are viewer state and never private viewing permissions."""

from dataclasses import dataclass


class SubscriptionAuthenticationRequired(Exception):
    pass


class SubscriptionError(Exception):
    pass


@dataclass(frozen=True)
class SubscriptionState:
    subscribed: bool
    can_subscribe: bool
