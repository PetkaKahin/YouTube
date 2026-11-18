"""Staff and report scenarios only for the isolated --moderation browser fixture."""

from uuid import uuid4

from sqlalchemy import text
from watch_seed import container

from app.resources import Resources
from channel.public import Register, Registration
from moderation.public import GetReports, SubmitReport
from user.public import SetRole


async def seed_moderation(manifest):
    app = container()
    try:

        async def invoke(scenario, *args):
            async with app() as operation:
                return await (await operation.get(scenario))(*args)

        resources = await app.get(Resources)
        async with resources.sessions() as session:
            await session.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence('moderation_reports','id'),"
                    "9007199254740992,false)"
                )
            )
            await session.commit()
        await invoke(SetRole, manifest["people"]["stranger"]["email"], "moderator")
        await invoke(SetRole, manifest["people"]["owner"]["email"], "admin")
        for _i in range(41):
            token = uuid4().hex
            account = await invoke(
                Register,
                Registration("Отправитель", f"{token}@example.com", token[:16], "abcdef", "abcdef"),
            )
            await invoke(
                SubmitReport, account.account_id, int(manifest["videos"]["public"]), "spam"
            )
        await invoke(
            SubmitReport,
            int(manifest["people"]["invited"]["id"]),
            int(manifest["videos"]["private"]),
            "other",
        )
        page = await invoke(GetReports, int(manifest["people"]["stranger"]["id"]))
        cursor = page.next_cursor
        rows = list(page.items)
        while cursor:
            page = await invoke(GetReports, int(manifest["people"]["stranger"]["id"]), cursor)
            rows.extend(page.items)
            cursor = page.next_cursor
        manifest["reports"] = {"public": str(rows[0].id), "private": str(rows[-1].id)}
    finally:
        await app.close()


async def control(manifest, action):
    app = container()
    try:
        async with app() as operation:
            if action in ("user", "moderator", "admin"):
                await (await operation.get(SetRole))(
                    manifest["people"]["stranger"]["email"], action
                )
            elif action == "repeat":
                await (await operation.get(SubmitReport))(
                    int(manifest["people"]["invited"]["id"]),
                    int(manifest["videos"]["private"]),
                    "other",
                )
            elif action == "restore_silent":
                resources = await app.get(Resources)
                async with resources.sessions.begin() as session:
                    await session.execute(
                        text("UPDATE video_publications SET moderation='visible' WHERE id=:id"),
                        {"id": int(manifest["videos"]["silent"])},
                    )
            else:
                raise ValueError("Invalid fixture command")
        return {"done": True}
    finally:
        await app.close()
