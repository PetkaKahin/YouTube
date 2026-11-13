"""Register shared problem schemas and the CSRF header in Swagger UI."""

from fastapi import FastAPI

from common.http.schemas import Problem


def configure_openapi(application: FastAPI) -> None:
    original_openapi = application.openapi

    def openapi():
        if application.openapi_schema is not None:
            return application.openapi_schema
        schema = original_openapi()
        components = schema.setdefault("components", {})
        schemas = components.setdefault("schemas", {})
        problem_schema = Problem.model_json_schema(ref_template="#/components/schemas/{model}")
        schemas.update(problem_schema.pop("$defs", {}))
        schemas["Problem"] = problem_schema
        components.setdefault("securitySchemes", {})["CSRFToken"] = {
            "type": "apiKey",
            "in": "header",
            "name": "X-CSRFToken",
            "description": "Copy the csrftoken cookie value before an unsafe request.",
        }
        for path in schema.get("paths", {}).values():
            for method, operation in path.items():
                if method in {"post", "put", "patch", "delete"}:
                    operation.setdefault("security", []).append({"CSRFToken": []})
        return schema

    application.openapi = openapi
