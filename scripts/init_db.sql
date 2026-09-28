-- ============================================================
-- EduAgent PostgreSQL 数据库初始化脚本
-- Docker 启动时自动执行（挂载到 /docker-entrypoint-initdb.d/）
-- ============================================================

-- 启用 UUID 自动生成扩展
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ============================================================
-- 用户与权限表
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    username        VARCHAR(64) NOT NULL,
    email           VARCHAR(128) NOT NULL,
    password_hash   VARCHAR(256) NOT NULL,
    role            VARCHAR(16) NOT NULL CHECK (role IN ('student', 'teacher', 'admin')),
    class_id        UUID,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tenant_id, email)
);
CREATE INDEX IF NOT EXISTS idx_users_tenant_id ON users (tenant_id);
CREATE INDEX IF NOT EXISTS idx_users_role ON users (role);
CREATE INDEX IF NOT EXISTS idx_users_class_id ON users (class_id);

-- ============================================================
-- 知识库待补充队列
-- ============================================================
CREATE TABLE IF NOT EXISTS knowledge_pending_queue (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    question        TEXT NOT NULL,
    student_id      UUID REFERENCES users(id),
    confidence      FLOAT NOT NULL,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'resolved', 'dismissed')),
    resolved_by     UUID REFERENCES users(id),
    resolved_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_knowledge_pending_queue_tenant_id ON knowledge_pending_queue (tenant_id);
CREATE INDEX IF NOT EXISTS idx_knowledge_pending_queue_status ON knowledge_pending_queue (status);

-- ============================================================
-- 试卷批改相关表
-- ============================================================
CREATE TABLE IF NOT EXISTS exams (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    title           VARCHAR(256) NOT NULL,
    description     TEXT,
    due_date        TIMESTAMPTZ,
    created_by      UUID REFERENCES users(id),
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_exams_tenant_id ON exams (tenant_id);

CREATE TABLE IF NOT EXISTS questions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    exam_id         UUID REFERENCES exams(id) ON DELETE CASCADE,
    question_no     INT NOT NULL,
    question_type   VARCHAR(16) NOT NULL
                    CHECK (question_type IN ('single_choice', 'multi_choice', 'judge', 'short_answer', 'code')),
    content         TEXT NOT NULL,
    correct_answer  TEXT,
    score           INT NOT NULL DEFAULT 10,
    knowledge_tag   VARCHAR(128),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_questions_exam_id ON questions (exam_id);
CREATE INDEX IF NOT EXISTS idx_questions_knowledge_tag ON questions (knowledge_tag);

CREATE TABLE IF NOT EXISTS scoring_points (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    question_id     UUID REFERENCES questions(id) ON DELETE CASCADE,
    point_desc      TEXT NOT NULL,
    point_score     INT NOT NULL,
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    confirmed_by    UUID REFERENCES users(id),
    confirmed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_scoring_points_question_id ON scoring_points (question_id);

CREATE TABLE IF NOT EXISTS exam_submissions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    exam_id         UUID REFERENCES exams(id),
    student_id      UUID REFERENCES users(id),
    source          VARCHAR(16) NOT NULL DEFAULT 'word'
                    CHECK (source IN ('word', 'online', 'miniapp')),
    word_minio_path VARCHAR(512),
    status          VARCHAR(16) NOT NULL DEFAULT 'submitted'
                    CHECK (status IN ('submitted', 'ai_processing', 'pending_review', 'reviewed', 'published')),
    submitted_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    published_at    TIMESTAMPTZ,
    weak_points          JSONB,
    weak_points_summary  TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (exam_id, student_id)
);
CREATE INDEX IF NOT EXISTS idx_exam_submissions_tenant_id ON exam_submissions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_exam_submissions_exam_id ON exam_submissions (exam_id);
CREATE INDEX IF NOT EXISTS idx_exam_submissions_student_id ON exam_submissions (student_id);
CREATE INDEX IF NOT EXISTS idx_exam_submissions_status ON exam_submissions (status);

CREATE TABLE IF NOT EXISTS exam_reviews (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    submission_id   UUID REFERENCES exam_submissions(id) ON DELETE CASCADE,
    question_id     UUID REFERENCES questions(id),
    question_type   VARCHAR(16) NOT NULL,
    knowledge_tag   VARCHAR(128),
    student_answer  TEXT,
    ai_score        INT,
    ai_feedback     TEXT,
    ai_raw_result   JSONB,
    teacher_score   INT,
    teacher_comment TEXT,
    final_score     INT,
    needs_review    BOOLEAN NOT NULL DEFAULT FALSE,
    reviewed_by     UUID REFERENCES users(id),
    reviewed_at     TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_exam_reviews_submission_id ON exam_reviews (submission_id);
CREATE INDEX IF NOT EXISTS idx_exam_reviews_needs_review ON exam_reviews (needs_review);
CREATE INDEX IF NOT EXISTS idx_exam_reviews_knowledge_tag ON exam_reviews (knowledge_tag);

-- ============================================================
-- 备课教学相关表（知识点体系 / 教案模板 / 历史教案 / 配套习题库 / 关联 / 日志）
-- ============================================================

-- 旧备课题库表已废弃，合并进 exercise_bank
DROP TABLE IF EXISTS lesson_questions;

-- 知识点体系表（需求解析节点做知识点拆解、匹配的基础库）
CREATE TABLE IF NOT EXISTS knowledge_points (
    kp_id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    subject           VARCHAR(64) NOT NULL,
    grade             VARCHAR(32) NOT NULL,
    kp_name           VARCHAR(128) NOT NULL,
    parent_kp_id      UUID REFERENCES knowledge_points(kp_id),
    difficulty_level  VARCHAR(8) CHECK (difficulty_level IN ('easy', 'medium', 'hard')),
    requirement_level VARCHAR(8) CHECK (requirement_level IN ('了解', '理解', '掌握', '运用')),  -- 课标要求层级
    curriculum_code   VARCHAR(64),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_knowledge_points_subject_grade ON knowledge_points (subject, grade);
CREATE INDEX IF NOT EXISTS idx_knowledge_points_parent_kp_id ON knowledge_points (parent_kp_id);

-- 教案模板表（不同学科/课型的标准教案结构）
CREATE TABLE IF NOT EXISTS lesson_templates (
    template_id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    subject            VARCHAR(64) NOT NULL,
    grade              VARCHAR(32) NOT NULL,
    lesson_type        VARCHAR(16) NOT NULL
                       CHECK (lesson_type IN ('new', 'exercise', 'review')),  -- 新授课/习题课/复习课
    template_structure JSONB NOT NULL,
    created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lesson_templates_subject_grade ON lesson_templates (subject, grade);

-- 历史教案主表（持久化完整生成教案）
CREATE TABLE IF NOT EXISTS lesson_plans (
    lesson_id      UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    teacher_id     UUID REFERENCES users(id),
    subject        VARCHAR(64) NOT NULL,
    grade          VARCHAR(32) NOT NULL,
    topic          VARCHAR(128) NOT NULL,
    duration       INT,                          -- 课时（分钟）
    lesson_content JSONB NOT NULL,               -- 结构化教案（目标/重难点/流程/板书/习题）
    status         VARCHAR(16) NOT NULL DEFAULT 'draft'
                   CHECK (status IN ('draft', 'formal', 'published', 'archived')),  -- 草稿/正式/已发布/已归档
    version        INT NOT NULL DEFAULT 1,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_lesson_plans_teacher_id ON lesson_plans (teacher_id);
CREATE INDEX IF NOT EXISTS idx_lesson_plans_subject_grade ON lesson_plans (subject, grade);

-- 配套习题库表（结构化存储习题，支撑习题匹配节点精确筛选）
CREATE TABLE IF NOT EXISTS exercise_bank (
    exercise_id    UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id      VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    subject        VARCHAR(64),
    grade          VARCHAR(32),
    topic          VARCHAR(128),
    kp_id          UUID REFERENCES knowledge_points(kp_id),
    question_type  VARCHAR(16) NOT NULL,
    content        TEXT NOT NULL,                -- 题干
    options        JSONB,                        -- 选择题选项
    answer         TEXT,                         -- 答案
    analysis       TEXT,                         -- 解析
    difficulty     VARCHAR(8) CHECK (difficulty IN ('easy', 'medium', 'hard')),
    score          INT NOT NULL DEFAULT 10,
    knowledge_tag  VARCHAR(128),
    quality_status VARCHAR(16) NOT NULL DEFAULT 'draft'
                   CHECK (quality_status IN ('draft', 'checked', 'settled')),  -- 草稿/已质检/已沉淀
    created_by     UUID REFERENCES users(id),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_exercise_bank_kp_id ON exercise_bank (kp_id);
CREATE INDEX IF NOT EXISTS idx_exercise_bank_subject_grade ON exercise_bank (subject, grade);
CREATE INDEX IF NOT EXISTS idx_exercise_bank_quality_status ON exercise_bank (quality_status);

-- 教案-知识点关联表（记录教案覆盖的知识点）
CREATE TABLE IF NOT EXISTS lesson_knowledge_rel (
    id        UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lesson_id UUID NOT NULL REFERENCES lesson_plans(lesson_id) ON DELETE CASCADE,
    kp_id     UUID NOT NULL REFERENCES knowledge_points(kp_id) ON DELETE CASCADE,
    UNIQUE (lesson_id, kp_id)
);
CREATE INDEX IF NOT EXISTS idx_lesson_knowledge_rel_kp_id ON lesson_knowledge_rel (kp_id);

-- 教案-习题关联表（记录教案选用的习题及用途）
CREATE TABLE IF NOT EXISTS lesson_exercise_rel (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    lesson_id   UUID NOT NULL REFERENCES lesson_plans(lesson_id) ON DELETE CASCADE,
    exercise_id UUID NOT NULL REFERENCES exercise_bank(exercise_id) ON DELETE CASCADE,
    usage_type  VARCHAR(16) NOT NULL DEFAULT 'class'
                CHECK (usage_type IN ('class', 'homework')),  -- 课堂练习/课后作业
    UNIQUE (lesson_id, exercise_id)
);
CREATE INDEX IF NOT EXISTS idx_lesson_exercise_rel_exercise_id ON lesson_exercise_rel (exercise_id);

-- 教师备课操作表（记录教师创建/修改/使用教案的行为）
CREATE TABLE IF NOT EXISTS teacher_lesson_history (
    id           UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    teacher_id   UUID NOT NULL REFERENCES users(id),
    lesson_id    UUID REFERENCES lesson_plans(lesson_id) ON DELETE CASCADE,
    action       VARCHAR(16) NOT NULL
                 CHECK (action IN ('create', 'update', 'use', 'export')),
    operate_time TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_teacher_lesson_history_teacher_id ON teacher_lesson_history (teacher_id);
CREATE INDEX IF NOT EXISTS idx_teacher_lesson_history_lesson_id ON teacher_lesson_history (lesson_id);

-- 教材目录表（可选，匹配课文主题到对应知识点）
CREATE TABLE IF NOT EXISTS textbook_catalog (
    catalog_id  UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    subject     VARCHAR(64) NOT NULL,
    grade       VARCHAR(32) NOT NULL,
    unit_name   VARCHAR(128) NOT NULL,
    lesson_name VARCHAR(128) NOT NULL,
    kp_id       UUID REFERENCES knowledge_points(kp_id),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_textbook_catalog_subject_grade ON textbook_catalog (subject, grade);

-- ============================================================
-- 学情分析相关表（作业布置 / 作答记录 / 干预策略库 / 学情报告）
-- ============================================================

-- 作业布置主表（作业模块；教师布置，学生提交作答回流 student_practice_records）
CREATE TABLE IF NOT EXISTS assignments (
    id         UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id  VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    teacher_id UUID REFERENCES users(id),
    class_id   UUID,                         -- 裸 UUID，对齐 users.class_id
    subject    VARCHAR(64) NOT NULL,
    grade      VARCHAR(32) NOT NULL,
    title      VARCHAR(128) NOT NULL,
    status     VARCHAR(16) NOT NULL DEFAULT 'published'
               CHECK (status IN ('draft', 'published', 'closed')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_assignments_class_id ON assignments (class_id);
CREATE INDEX IF NOT EXISTS idx_assignments_teacher_id ON assignments (teacher_id);

-- 作业-习题关联表（作业选用哪些习题，seq 为题号顺序）
CREATE TABLE IF NOT EXISTS assignment_exercises (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    assignment_id UUID NOT NULL REFERENCES assignments(id) ON DELETE CASCADE,
    exercise_id   UUID NOT NULL REFERENCES exercise_bank(exercise_id),
    seq           INT NOT NULL DEFAULT 1,
    UNIQUE (assignment_id, exercise_id)
);
CREATE INDEX IF NOT EXISTS idx_assignment_exercises_assignment_id ON assignment_exercises (assignment_id);

-- 日常作业/习题作答记录表（学情分析数据源；考试数据走 exam_reviews，不进此表）
CREATE TABLE IF NOT EXISTS student_practice_records (
    id            UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id     VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    student_id    UUID REFERENCES users(id),
    exercise_id   UUID REFERENCES exercise_bank(exercise_id),
    assignment_id UUID,                          -- 关联作业布置（作业模块后续建，预留 seam②）
    kp_id         UUID REFERENCES knowledge_points(kp_id),  -- 归一化后回填
    knowledge_tag VARCHAR(128),                  -- 原始自由文本知识点标签（归一化前）
    subject       VARCHAR(64) NOT NULL,
    grade         VARCHAR(32) NOT NULL,
    source        VARCHAR(16) NOT NULL DEFAULT 'homework'
                  CHECK (source IN ('homework', 'practice', 'quiz')),  -- 作业/练习/小测
    answer        TEXT,                          -- 学生作答
    is_correct    BOOLEAN,                       -- 自动判分结果
    score         INT,
    recorded_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_student_practice_records_tenant_id ON student_practice_records (tenant_id);
CREATE INDEX IF NOT EXISTS idx_student_practice_records_student_id ON student_practice_records (student_id);
CREATE INDEX IF NOT EXISTS idx_student_practice_records_kp_id ON student_practice_records (kp_id);
CREATE INDEX IF NOT EXISTS idx_student_practice_records_subject_grade ON student_practice_records (subject, grade);

-- 干预策略库（分层教学 / 错题补救策略，支撑干预策略检索节点）
CREATE TABLE IF NOT EXISTS intervention_strategies (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id        VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    subject          VARCHAR(64),
    grade            VARCHAR(32),
    strat_level      VARCHAR(16) NOT NULL DEFAULT 'all'
                     CHECK (strat_level IN ('excellent', 'good', 'weak', 'all')),  -- 适用学生分层
    error_type       VARCHAR(32) NOT NULL DEFAULT 'all'
                     CHECK (error_type IN ('concept', 'calculation', 'reading', 'method', 'all')),  -- 错题类型
    kp_id            UUID REFERENCES knowledge_points(kp_id),  -- 关联知识点（可空）
    source_lesson_id UUID REFERENCES lesson_plans(lesson_id),  -- 关联优秀教案（seam③，可空）
    strategy_content TEXT NOT NULL,             -- 策略内容
    applicable_scene TEXT,                      -- 适用场景说明
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_intervention_strategies_subject_grade ON intervention_strategies (subject, grade);
CREATE INDEX IF NOT EXISTS idx_intervention_strategies_strat_level ON intervention_strategies (strat_level);

-- 学情报告主表（结构化报告 + 中间结果 metrics 内嵌 JSONB）
CREATE TABLE IF NOT EXISTS learning_reports (
    id             UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id      VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    teacher_id     UUID REFERENCES users(id),
    class_id       UUID,                         -- 班级（裸 UUID，对齐 users.class_id）
    subject        VARCHAR(64) NOT NULL,
    grade          VARCHAR(32) NOT NULL,
    report_type    VARCHAR(16) NOT NULL DEFAULT 'class'
                   CHECK (report_type IN ('class', 'student')),   -- 班级/个人
    template       VARCHAR(16) NOT NULL DEFAULT 'primary'
                   CHECK (template IN ('primary', 'junior', 'senior')),     -- 小学/初中/高中
    scope          JSONB,                        -- 数据范围（时间范围/考试范围）
    metrics        JSONB,                        -- 中间结果（掌握度/分层/错题）
    report_content JSONB,                        -- 结构化报告正文
    status         VARCHAR(16) NOT NULL DEFAULT 'draft'
                   CHECK (status IN ('draft', 'published')),
    created_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_learning_reports_tenant_id ON learning_reports (tenant_id);
CREATE INDEX IF NOT EXISTS idx_learning_reports_teacher_id ON learning_reports (teacher_id);
CREATE INDEX IF NOT EXISTS idx_learning_reports_class_id ON learning_reports (class_id);
CREATE INDEX IF NOT EXISTS idx_learning_reports_subject_grade ON learning_reports (subject, grade);

-- ============================================================
-- 简历审查相关表
-- ============================================================
CREATE TABLE IF NOT EXISTS resume_reviews (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    student_id      UUID REFERENCES users(id),
    pdf_minio_path  VARCHAR(512) NOT NULL,
    structured_data JSONB,
    scores          JSONB,
    issues          JSONB,
    summary         JSONB,
    status          VARCHAR(16) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'processing', 'done', 'failed')),
    error_msg       TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_resume_reviews_tenant_id ON resume_reviews (tenant_id);
CREATE INDEX IF NOT EXISTS idx_resume_reviews_student_id ON resume_reviews (student_id);
CREATE INDEX IF NOT EXISTS idx_resume_reviews_status ON resume_reviews (status);

-- ============================================================
-- 模拟面试相关表
-- ============================================================
CREATE TABLE IF NOT EXISTS interview_questions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    content         TEXT NOT NULL,
    difficulty      VARCHAR(8) NOT NULL DEFAULT 'medium'
                    CHECK (difficulty IN ('easy', 'medium', 'hard')),
    tags            JSONB NOT NULL DEFAULT '[]',
    target_position VARCHAR(128) NOT NULL DEFAULT 'general',
    is_active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_by      UUID REFERENCES users(id),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_interview_questions_tenant_id       ON interview_questions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_interview_questions_target_position ON interview_questions (target_position);
CREATE INDEX IF NOT EXISTS idx_interview_questions_difficulty      ON interview_questions (difficulty);
CREATE INDEX IF NOT EXISTS idx_interview_questions_is_active       ON interview_questions (is_active);

CREATE TABLE IF NOT EXISTS interview_sessions (
    id               UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id        VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    student_id       UUID REFERENCES users(id),
    session_id       VARCHAR(128) NOT NULL,
    thread_id        VARCHAR(128) NOT NULL UNIQUE,
    target_position  VARCHAR(128) NOT NULL DEFAULT '',
    resume_review_id UUID REFERENCES resume_reviews(id),
    summary          TEXT,
    report           JSONB,
    overall_score    INT,
    status           VARCHAR(16) NOT NULL DEFAULT 'in_progress'
                     CHECK (status IN ('in_progress', 'finished')),
    finished_at      TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_interview_sessions_tenant_id  ON interview_sessions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_interview_sessions_student_id ON interview_sessions (student_id);
CREATE INDEX IF NOT EXISTS idx_interview_sessions_session_id ON interview_sessions (session_id);
CREATE INDEX IF NOT EXISTS idx_interview_sessions_status     ON interview_sessions (status);

-- ============================================================
-- 问答会话表
-- ============================================================
CREATE TABLE IF NOT EXISTS qa_sessions (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    tenant_id       VARCHAR(64) NOT NULL DEFAULT 'tenant_default',
    student_id      UUID REFERENCES users(id),
    thread_id       VARCHAR(128) NOT NULL UNIQUE,
    summary         TEXT,
    summary_version INT NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_qa_sessions_tenant_id ON qa_sessions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_qa_sessions_student_id ON qa_sessions (student_id);
CREATE INDEX IF NOT EXISTS idx_qa_sessions_thread_id ON qa_sessions (thread_id);

-- ============================================================
-- 自动更新 updated_at 触发器
-- ============================================================
CREATE OR REPLACE FUNCTION update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'users',
        'exams', 'exam_submissions', 'exam_reviews',
        'resume_reviews', 'interview_sessions',
        'interview_questions', 'qa_sessions',
        'lesson_templates', 'lesson_plans', 'exercise_bank',
        'student_practice_records', 'intervention_strategies', 'learning_reports'
    ]
    LOOP
        EXECUTE format('
            DROP TRIGGER IF EXISTS trg_%s_updated_at ON %s;
            CREATE TRIGGER trg_%s_updated_at
            BEFORE UPDATE ON %s
            FOR EACH ROW EXECUTE FUNCTION update_updated_at_column();
        ', t, t, t, t);
    END LOOP;
END;
$$;