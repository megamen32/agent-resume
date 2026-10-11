"""Fast unit: scoped installer grant preserves config and exact source symlink (1s/max 5s)."""
import importlib.util
from pathlib import Path
import yaml


def test_installer_merges_supported_entry_and_preserves_live_config(tmp_path):
    source=Path(__file__).parents[1]/"scripts/install-hermes-plugin.py"
    spec=importlib.util.spec_from_file_location("install_hermes_tasks",source)
    mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
    config={"model":{"default":"GLM"},"private":{"sentinel":"preserve"},"plugins":{"enabled":["other"],"disabled":["agent-resume"],"entries":{"other":{"allow_gateway_injection":False},"agent-resume":{"allow_tool_override":False}}}}
    actual=tmp_path/"canonical.yaml";actual.write_text(yaml.safe_dump(config))
    (tmp_path/"config.yaml").symlink_to(actual)
    key="agent:main:telegram:group:-1004360838692:9"
    mod.configure_gateway_tasks(tmp_path,key)
    result=yaml.safe_load(actual.read_text())
    assert (tmp_path/"config.yaml").is_symlink()
    assert result['model']==config['model'] and result['private']==config['private']
    assert result['plugins']['entries']['other']==config['plugins']['entries']['other']
    assert result['plugins']['entries']['agent-resume']=={'allow_tool_override':False,'allow_gateway_injection':True,'gateway_task_session_key':key}
    assert result['plugins']['enabled']==['other','agent-resume'] and result['plugins']['disabled']==[]
    plugin=mod.install(tmp_path,Path(__file__).parents[1]/"hermes_gateway.py")
    assert (plugin/'__init__.py').resolve()==(Path(__file__).parents[1]/'hermes_gateway.py').resolve()
    assert yaml.safe_load((plugin/'plugin.yaml').read_text())['hooks']==['pre_gateway_dispatch','pre_llm_call']
