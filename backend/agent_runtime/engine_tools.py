"""Public inventory derived from the same schemas that providers receive."""
from backend.contracts.engine import EngineTool, EngineToolCatalog
from .capability_profiles import ASK_PROFILE, PLAN_PROFILE, EDIT_PROFILE, BUILD_ONLY_PROFILE, BUILD_REPAIR_PROFILE, SIMULATE_PROFILE
from .tool_contracts import ToolName, WRITE_TOOLS, EXECUTION_TOOLS, provider_function_schema


def engine_tool_catalog():
    profiles = (ASK_PROFILE, PLAN_PROFILE, EDIT_PROFILE, BUILD_ONLY_PROFILE, BUILD_REPAIR_PROFILE, SIMULATE_PROFILE)
    tools = []
    for name in ToolName:
        function = provider_function_schema(name)["function"]
        tools.append(EngineTool(name=name, provider_name=function["name"], description=function["description"],
            arguments=function["parameters"], stages_files=name in WRITE_TOOLS,
            executes_process=name in EXECUTION_TOOLS and name != ToolName.OBSERVE_SERIAL,
            capability_profiles=[profile.profile_id for profile in profiles if name in profile.allowed_tools]))
    return EngineToolCatalog(tools=tools)
