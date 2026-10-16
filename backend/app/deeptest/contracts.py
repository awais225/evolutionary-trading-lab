"""Additive V9.3 request contracts; validation never enables trading."""
from typing import Literal
from pydantic import BaseModel,Field,ConfigDict
class BulkDeepRequest(BaseModel):
    model_config=ConfigDict(extra='forbid')
    node_ids:list[int]=Field(min_length=1,max_length=5000)
    duration_key:str='3M'
    settings:dict=Field(default_factory=dict)
    node_settings:dict=Field(default_factory=dict)
    node_settings_by_id:dict[str,dict]=Field(default_factory=dict)
    retry_incomplete:bool=False
class ScheduleRequest(BaseModel):
    target:Literal['deep','live']='deep'
    rules:dict
    name:str=Field(default='',max_length=120)
    expected_scenario_id:str|None=None
class SelectionRequest(BaseModel):
    node_ids:list[int]=Field(min_length=1,max_length=5000)
