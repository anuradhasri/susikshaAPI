"""Structured, child-based goal and observation sheets (no booking/session dependency)."""
from sqlalchemy import Column, Integer, String, Text, Date, DateTime, ForeignKey, UniqueConstraint, Float
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.core.database import Base


class SheetAudit:
    id = Column(Integer, primary_key=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())
    created_by = Column(Integer, ForeignKey('users.id'), nullable=False)
    updated_by = Column(Integer, ForeignKey('users.id'), nullable=False)


class GoalLevelMaster(Base):
    __tablename__ = 'goal_level_master'
    id = Column(Integer, primary_key=True)
    name = Column(String(100), nullable=False, unique=True)
    sort_order = Column(Integer, nullable=False, default=0)


class GoalTitleMaster(Base):
    __tablename__ = 'goal_title_master'
    id = Column(Integer, primary_key=True)
    level_id = Column(Integer, ForeignKey('goal_level_master.id'), nullable=False, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text, nullable=False)
    __table_args__ = (UniqueConstraint('level_id', 'title'),)


class GoalDomainMaster(Base):
    __tablename__ = 'goal_domain_master'
    id = Column(Integer, primary_key=True)
    level_id = Column(Integer, ForeignKey('goal_level_master.id'), nullable=False, index=True)
    code = Column(String(20), nullable=False)
    description = Column(Text, nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    __table_args__ = (UniqueConstraint('level_id', 'code'),)


class GoalSkillMaster(Base):
    __tablename__ = 'goal_skill_master'
    id = Column(Integer, primary_key=True)
    level_id = Column(Integer, ForeignKey('goal_level_master.id'), index=True)
    domain_id = Column(Integer, ForeignKey('goal_domain_master.id'), index=True)
    code = Column(String(50), nullable=False)
    description = Column(Text, nullable=False)


class GoalTitleSkillMapping(Base):
    __tablename__ = 'goal_title_skill_mapping'
    id = Column(Integer, primary_key=True)
    title_id = Column(Integer, ForeignKey('goal_title_master.id'), nullable=False, index=True)
    skill_id = Column(Integer, ForeignKey('goal_skill_master.id'), nullable=False)
    __table_args__ = (UniqueConstraint('title_id', 'skill_id'),)


class GoalObjectiveTypeMaster(Base):
    __tablename__ = 'goal_objective_type_master'
    id = Column(Integer, primary_key=True)
    code = Column(String(20), nullable=False, unique=True)
    name = Column(String(100), nullable=False)


class PatientGoalSheet(SheetAudit, Base):
    __tablename__ = 'patient_goal_sheet'
    patient_id = Column(Integer, ForeignKey('patients.id'), nullable=False, index=True)
    region_id = Column(Integer, ForeignKey('regions.id'), nullable=False, index=True)
    planning_month = Column(Date, nullable=False, index=True)
    review_month = Column(Date, nullable=False)
    therapist_id = Column(Integer, ForeignKey('therapists.id'), nullable=False)
    parent_name = Column(String(255), nullable=False, default='')
    parental_objectives = Column(Text, nullable=False, default='')
    patient = relationship('Patient')
    therapist = relationship('Therapist')
    therapists = relationship('PatientGoalSheetTherapist', cascade='all, delete-orphan')
    region = relationship('Region')
    items = relationship('PatientGoalSheetItem', cascade='all, delete-orphan', order_by='PatientGoalSheetItem.sort_order')


class PatientGoalSheetTherapist(Base):
    __tablename__ = 'patient_goal_sheet_therapist'
    id = Column(Integer, primary_key=True)
    sheet_id = Column(Integer, ForeignKey('patient_goal_sheet.id'), nullable=False, index=True)
    therapist_id = Column(Integer, ForeignKey('therapists.id'), nullable=False)
    therapist = relationship('Therapist')
    __table_args__ = (UniqueConstraint('sheet_id', 'therapist_id'),)


class PatientGoalSheetItem(Base):
    __tablename__ = 'patient_goal_sheet_item'
    id = Column(Integer, primary_key=True)
    sheet_id = Column(Integer, ForeignKey('patient_goal_sheet.id'), nullable=False, index=True)
    level_id = Column(Integer, ForeignKey('goal_level_master.id'))
    title_id = Column(Integer, ForeignKey('goal_title_master.id'))
    description = Column(Text, nullable=False)
    sort_order = Column(Integer, nullable=False)
    skills = relationship('PatientGoalSheetItemSkill', cascade='all, delete-orphan')
    domains = relationship('PatientGoalSheetItemDomain', cascade='all, delete-orphan')
    objectives = relationship('PatientGoalSheetItemObjective', cascade='all, delete-orphan')


class PatientGoalSheetItemSkill(Base):
    __tablename__ = 'patient_goal_sheet_item_skill'
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey('patient_goal_sheet_item.id'), nullable=False, index=True)
    skill_id = Column(Integer, ForeignKey('goal_skill_master.id'), nullable=False)
    __table_args__ = (UniqueConstraint('item_id', 'skill_id'),)


class PatientGoalSheetItemDomain(Base):
    __tablename__ = 'patient_goal_sheet_item_domain'
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey('patient_goal_sheet_item.id'), nullable=False, index=True)
    domain_id = Column(Integer, ForeignKey('goal_domain_master.id'), nullable=False)
    __table_args__ = (UniqueConstraint('item_id', 'domain_id'),)


class PatientGoalSheetItemObjective(Base):
    __tablename__ = 'patient_goal_sheet_item_objective'
    id = Column(Integer, primary_key=True)
    item_id = Column(Integer, ForeignKey('patient_goal_sheet_item.id'), nullable=False, index=True)
    type_id = Column(Integer, ForeignKey('goal_objective_type_master.id'), nullable=False)
    text = Column(Text, nullable=False)
    __table_args__ = (UniqueConstraint('item_id', 'type_id'),)


class PatientObservationSheet(SheetAudit, Base):
    __tablename__ = 'patient_observation_sheet'
    patient_id = Column(Integer, ForeignKey('patients.id'), nullable=False, index=True)
    region_id = Column(Integer, ForeignKey('regions.id'), nullable=False, index=True)
    observation_date = Column(Date, nullable=False, index=True)
    patient = relationship('Patient')
    region = relationship('Region')
    therapists = relationship('PatientObservationSheetTherapist', cascade='all, delete-orphan')
    entries = relationship('PatientObservationSheetEntry', cascade='all, delete-orphan', order_by='PatientObservationSheetEntry.sort_order')


class PatientObservationSheetTherapist(Base):
    __tablename__ = 'patient_observation_sheet_therapist'
    id = Column(Integer, primary_key=True)
    sheet_id = Column(Integer, ForeignKey('patient_observation_sheet.id'), nullable=False, index=True)
    therapist_id = Column(Integer, ForeignKey('therapists.id'), nullable=False)
    therapist = relationship('Therapist')
    __table_args__ = (UniqueConstraint('sheet_id', 'therapist_id'),)


class PatientObservationSheetEntry(Base):
    __tablename__ = 'patient_observation_sheet_entry'
    id = Column(Integer, primary_key=True)
    sheet_id = Column(Integer, ForeignKey('patient_observation_sheet.id'), nullable=False, index=True)
    level_id = Column(Integer, ForeignKey('goal_level_master.id'))
    title_id = Column(Integer, ForeignKey('goal_title_master.id'))
    objective = Column(String(255), nullable=False, default='')
    goal = Column(Text, nullable=False)
    activities = Column(Text, nullable=False, default='')
    accuracy = Column(Float)
    accuracy_status = Column(String(2), nullable=False, default='NO')
    prompts = Column(String(100), nullable=False, default='')
    responses = Column(Text, nullable=False, default='')
    concerns = Column(Text, nullable=False, default='')
    strategy = Column(Text, nullable=False, default='')
    sort_order = Column(Integer, nullable=False)
    domains = relationship('PatientObservationSheetEntryDomain', cascade='all, delete-orphan')
    skills = relationship('PatientObservationSheetEntrySkill', cascade='all, delete-orphan')


class PatientObservationSheetEntryDomain(Base):
    __tablename__ = 'patient_observation_sheet_entry_domain'
    id = Column(Integer, primary_key=True)
    entry_id = Column(Integer, ForeignKey('patient_observation_sheet_entry.id'), nullable=False, index=True)
    domain_id = Column(Integer, ForeignKey('goal_domain_master.id'), nullable=False)
    __table_args__ = (UniqueConstraint('entry_id', 'domain_id'),)


class PatientObservationSheetEntrySkill(Base):
    __tablename__ = 'patient_observation_sheet_entry_skill'
    id = Column(Integer, primary_key=True)
    entry_id = Column(Integer, ForeignKey('patient_observation_sheet_entry.id'), nullable=False, index=True)
    skill_id = Column(Integer, ForeignKey('goal_skill_master.id'), nullable=False)
    __table_args__ = (UniqueConstraint('entry_id', 'skill_id'),)


REPORT_TABLES = [GoalLevelMaster, GoalTitleMaster, GoalDomainMaster, GoalSkillMaster, GoalTitleSkillMapping,
                GoalObjectiveTypeMaster, PatientGoalSheet, PatientGoalSheetTherapist, PatientGoalSheetItem,
                PatientGoalSheetItemDomain, PatientGoalSheetItemSkill, PatientGoalSheetItemObjective,
                PatientObservationSheet, PatientObservationSheetTherapist, PatientObservationSheetEntry,
                PatientObservationSheetEntryDomain, PatientObservationSheetEntrySkill]
