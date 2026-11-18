"""Discussion scenarios share the request transaction with Video and User adapters."""

from dishka import (
    Provider,
    Scope,
    provide,  # pyright: ignore[reportUnknownVariableType] -- upstream decorator overloads
)

from comment.application.comments import (
    CommentActions,
    CommentAuthors,
    Comments,
    CreateComment,
    DiscussionAccess,
    GetCommentLike,
    GetComments,
    SetCommentLike,
)
from comment.infrastructure.comments import SqlComments, UserCommentAuthors, VideoDiscussionAccess


class CommentProvider(Provider):
    scope = Scope.REQUEST

    access = provide(VideoDiscussionAccess, provides=DiscussionAccess)
    authors = provide(UserCommentAuthors, provides=CommentAuthors)
    comments = provide(SqlComments, provides=Comments)
    actions = provide(CommentActions)
    get_comments = provide(GetComments)
    create_comment = provide(CreateComment)
    get_comment_like = provide(GetCommentLike)
    set_comment_like = provide(SetCommentLike)
