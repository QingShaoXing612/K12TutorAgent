# backend/db/migrations.py
from sqlalchemy import text
from backend.dependencies import AsyncSessionLocal
from backend.core.logger import get_logger

logger = get_logger(__name__)

# 所有补丁，按时间顺序追加。SQL 必须幂等（带 IF NOT EXISTS）
_MIGRATIONS: list[tuple[str, str]] = [
    (
        "exam_submissions.weak_points",
        "ALTER TABLE exam_submissions ADD COLUMN IF NOT EXISTS weak_points JSONB",
    ),
    (
        "exam_reviews.knowledge_tag",
        "ALTER TABLE exam_reviews ADD COLUMN IF NOT EXISTS knowledge_tag VARCHAR(128)",
    ),
    (
        "idx_exam_submissions_student_created",
        "CREATE INDEX IF NOT EXISTS idx_exam_submissions_student_created "
        "ON exam_submissions (student_id, created_at DESC)",
    ),
    # ── 作业模块：2 张新表（对齐 init_db.sql「学情分析相关表」段）──
    (
        "assignments",
        "CREATE TABLE IF NOT EXISTS assignments ("
        "id UUID PRIMARY KEY DEFAULT uuid_generate_v4(), "
        "tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_default', "
        "teacher_id UUID REFERENCES users(id), class_id UUID, "
        "subject VARCHAR(64) NOT NULL, grade VARCHAR(32) NOT NULL, "
        "title VARCHAR(128) NOT NULL, "
        "status VARCHAR(16) NOT NULL DEFAULT 'published' "
        "CHECK (status IN ('draft','published','closed')), "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
    ),
    (
        "assignment_exercises",
        "CREATE TABLE IF NOT EXISTS assignment_exercises ("
        "id UUID PRIMARY KEY DEFAULT uuid_generate_v4(), "
        "assignment_id UUID NOT NULL REFERENCES assignments(id) ON DELETE CASCADE, "
        "exercise_id UUID NOT NULL REFERENCES exercise_bank(exercise_id), "
        "seq INT NOT NULL DEFAULT 1, "
        "UNIQUE (assignment_id, exercise_id))",
    ),
    (
        "idx_assignments_class_id",
        "CREATE INDEX IF NOT EXISTS idx_assignments_class_id ON assignments (class_id)",
    ),
    (
        "idx_assignment_exercises_assignment_id",
        "CREATE INDEX IF NOT EXISTS idx_assignment_exercises_assignment_id "
        "ON assignment_exercises (assignment_id)",
    ),
    # ── 学情分析：3 张新表（对齐 init_db.sql「学情分析相关表」段）──
    (
        "student_practice_records",
        "CREATE TABLE IF NOT EXISTS student_practice_records ("
        "id UUID PRIMARY KEY DEFAULT uuid_generate_v4(), "
        "tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_default', "
        "student_id UUID REFERENCES users(id), "
        "exercise_id UUID REFERENCES exercise_bank(exercise_id), "
        "assignment_id UUID, "
        "kp_id UUID REFERENCES knowledge_points(kp_id), "
        "knowledge_tag VARCHAR(128), "
        "subject VARCHAR(64) NOT NULL, "
        "grade VARCHAR(32) NOT NULL, "
        "source VARCHAR(16) NOT NULL DEFAULT 'homework' "
        "CHECK (source IN ('homework','practice','quiz')), "
        "answer TEXT, is_correct BOOLEAN, score INT, "
        "recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
    ),
    (
        "intervention_strategies",
        "CREATE TABLE IF NOT EXISTS intervention_strategies ("
        "id UUID PRIMARY KEY DEFAULT uuid_generate_v4(), "
        "tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_default', "
        "subject VARCHAR(64), grade VARCHAR(32), "
        "strat_level VARCHAR(16) NOT NULL DEFAULT 'all' "
        "CHECK (strat_level IN ('excellent','good','weak','all')), "
        "error_type VARCHAR(32) NOT NULL DEFAULT 'all' "
        "CHECK (error_type IN ('concept','calculation','reading','method','all')), "
        "kp_id UUID REFERENCES knowledge_points(kp_id), "
        "source_lesson_id UUID REFERENCES lesson_plans(lesson_id), "
        "strategy_content TEXT NOT NULL, applicable_scene TEXT, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
    ),
    (
        "learning_reports",
        "CREATE TABLE IF NOT EXISTS learning_reports ("
        "id UUID PRIMARY KEY DEFAULT uuid_generate_v4(), "
        "tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_default', "
        "teacher_id UUID REFERENCES users(id), class_id UUID, "
        "subject VARCHAR(64) NOT NULL, grade VARCHAR(32) NOT NULL, "
        "report_type VARCHAR(16) NOT NULL DEFAULT 'class' "
        "CHECK (report_type IN ('class','student')), "
        "template VARCHAR(16) NOT NULL DEFAULT 'primary' "
        "CHECK (template IN ('primary','junior','senior')), "
        "scope JSONB, metrics JSONB, report_content JSONB, "
        "status VARCHAR(16) NOT NULL DEFAULT 'draft' "
        "CHECK (status IN ('draft','published')), "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(), "
        "updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
    ),
    (
        "idx_student_practice_records_tenant_id",
        "CREATE INDEX IF NOT EXISTS idx_student_practice_records_tenant_id "
        "ON student_practice_records (tenant_id)",
    ),
    (
        "idx_student_practice_records_student_id",
        "CREATE INDEX IF NOT EXISTS idx_student_practice_records_student_id "
        "ON student_practice_records (student_id)",
    ),
    (
        "idx_student_practice_records_kp_id",
        "CREATE INDEX IF NOT EXISTS idx_student_practice_records_kp_id "
        "ON student_practice_records (kp_id)",
    ),
    (
        "idx_student_practice_records_subject_grade",
        "CREATE INDEX IF NOT EXISTS idx_student_practice_records_subject_grade "
        "ON student_practice_records (subject, grade)",
    ),
    (
        "idx_intervention_strategies_subject_grade",
        "CREATE INDEX IF NOT EXISTS idx_intervention_strategies_subject_grade "
        "ON intervention_strategies (subject, grade)",
    ),
    (
        "idx_intervention_strategies_strat_level",
        "CREATE INDEX IF NOT EXISTS idx_intervention_strategies_strat_level "
        "ON intervention_strategies (strat_level)",
    ),
    (
        "idx_learning_reports_tenant_id",
        "CREATE INDEX IF NOT EXISTS idx_learning_reports_tenant_id "
        "ON learning_reports (tenant_id)",
    ),
    (
        "idx_learning_reports_teacher_id",
        "CREATE INDEX IF NOT EXISTS idx_learning_reports_teacher_id "
        "ON learning_reports (teacher_id)",
    ),
    (
        "idx_learning_reports_subject_grade",
        "CREATE INDEX IF NOT EXISTS idx_learning_reports_subject_grade "
        "ON learning_reports (subject, grade)",
    ),
    (
        "idx_learning_reports_class_id",
        "CREATE INDEX IF NOT EXISTS idx_learning_reports_class_id "
        "ON learning_reports (class_id)",
    ),
    # ── 去掉 questions.question_type 的 code 枚举（K12 无代码题）──
    (
        "questions_question_type_drop_code",
        "DO $$ BEGIN "
        "IF EXISTS (SELECT 1 FROM pg_constraint "
        "           WHERE conname='questions_question_type_check' "
        "             AND pg_get_constraintdef(oid) LIKE '%code%') THEN "
        "ALTER TABLE questions DROP CONSTRAINT questions_question_type_check; "
        "ALTER TABLE questions ADD CONSTRAINT questions_question_type_check "
        "CHECK (question_type IN ('single_choice','multi_choice','judge','short_answer')); "
        "END IF; END $$;",
    ),
    # ── 考试科目维度：exams 加 subject 列（支撑考试源按科目过滤）──
    (
        "exams.subject",
        "ALTER TABLE exams ADD COLUMN IF NOT EXISTS subject VARCHAR(64)",
    ),
    (
        "exams.subject_backfill",
        "UPDATE exams SET subject = '数学' WHERE subject IS NULL",
    ),
    # ── 班级实体表（布置作业/学情报告下拉用：名称 + 年级 + 按年级过滤）──
    (
        "classes",
        "CREATE TABLE IF NOT EXISTS classes ("
        "id UUID PRIMARY KEY, "
        "tenant_id VARCHAR(64) NOT NULL DEFAULT 'tenant_default', "
        "name VARCHAR(64) NOT NULL, "
        "grade VARCHAR(32) NOT NULL, "
        "created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())",
    ),
    # …… 每次给 init_db.sql 加字段，就在这里同步追加一条
]


async def run_migrations() -> None:
    """应用启动时执行所有 Schema 补丁；单条失败只警告，不阻断启动。"""
    async with AsyncSessionLocal() as session:
        for desc, sql in _MIGRATIONS:
            try:
                await session.execute(text(sql))
                await session.commit()
            except Exception as e:
                await session.rollback()
                if "already exists" not in str(e):
                    logger.warning("db.migration_failed", column=desc, error=str(e))
    logger.info("db.migrations_done", count=len(_MIGRATIONS))
