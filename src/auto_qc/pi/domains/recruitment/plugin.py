"""招聘电话场景的领域插件实现。"""
from auto_qc.pi.domains.base import DomainPlugin, DataAdapter
from auto_qc.pi.domains.recruitment.adapter import RecruitmentAdapter
from auto_qc.pi.domains.recruitment.criteria import RECRUITMENT_CRITERIA


class RecruitmentDomain(DomainPlugin):
    name = "recruitment"

    @property
    def data_adapter(self) -> DataAdapter:
        return RecruitmentAdapter()

    @property
    def quality_criteria(self) -> dict:
        return RECRUITMENT_CRITERIA


# 向后兼容：旧代码可能直接 import Domain
Domain = RecruitmentDomain
