SET NAMES utf8mb4;
CREATE DATABASE IF NOT EXISTS decision_loop CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;
CREATE TABLE decision_loop.decision_record (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    order_id INT NOT NULL,
    event_id VARCHAR(20) NOT NULL,
    evidence JSON NOT NULL,
    agent_note TEXT NOT NULL,
    description TEXT NOT NULL,
    approval_status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    execution_status VARCHAR(20) DEFAULT NULL,
    occupancy TINYINT DEFAULT 1,
    invalidation_reason TEXT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY one_occupied_combination (order_id,event_id,occupancy),
    CONSTRAINT valid_occupancy CHECK (occupancy IS NULL OR occupancy=1),
    CONSTRAINT valid_approval_status CHECK (approval_status IN ('PENDING','APPROVED','REJECTED','INVALIDATED')),
    CONSTRAINT valid_execution_status CHECK (
        execution_status IS NULL
        OR execution_status IN ('NOT_STARTED','SUCCEEDED','FAILED','BLOCKED')
    )
) ENGINE=InnoDB;

CREATE USER 'decision_agent'@'%' IDENTIFIED BY 'demo_agent_db_password';
GRANT SELECT ON decision_loop.decision_record TO 'decision_agent'@'%';
GRANT INSERT (order_id,event_id,evidence,agent_note,description)
    ON decision_loop.decision_record TO 'decision_agent'@'%';

-- Personal review identity and append-only conclusions.
CREATE TABLE decision_loop.app_user (
    id BIGINT NOT NULL PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    role VARCHAR(40) NOT NULL,
    enabled BOOLEAN NOT NULL,
    token_hash CHAR(64) CHARACTER SET ascii COLLATE ascii_bin NOT NULL UNIQUE
) ENGINE=InnoDB;
CREATE TABLE decision_loop.approval_record (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    decision_id BIGINT NOT NULL UNIQUE,
    conclusion VARCHAR(20) NOT NULL,
    actor_id BIGINT NOT NULL,
    actor_name VARCHAR(100) NOT NULL,
    actor_role VARCHAR(40) NOT NULL,
    comment TEXT NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    FOREIGN KEY (decision_id) REFERENCES decision_loop.decision_record(id),
    FOREIGN KEY (actor_id) REFERENCES decision_loop.app_user(id),
    CHECK (conclusion IN ('APPROVE','REJECT'))
) ENGINE=InnoDB;
INSERT INTO decision_loop.app_user VALUES (1,'演示采购主管','采购主管',1,'f323680f35f7c1b6104f518b7333e4bbfd3aec262c705323ab43c9d73fe212cc');
INSERT INTO decision_loop.app_user VALUES (2,'演示采购员','采购员',1,'5cfdfbeb64fbc8d045f7359fbe0fdc63c6cf11316fe7ff49c32926ce7b63cbda');
INSERT INTO decision_loop.app_user VALUES (3,'停用用户','采购主管',0,'36a9248967281295510370ba6c018f4e6de8fe1a46fa5f558013f96af8536b54');

-- 供应风险复核任务放在采购库。来源决策只做唯一约束，不以外键限制决策记录。
CREATE TABLE procurement.supply_risk_review_task (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    source_decision_id BIGINT NOT NULL,
    approval_id BIGINT NOT NULL,
    order_id INT NOT NULL,
    event_id VARCHAR(20) NOT NULL,
    assigned_role VARCHAR(40) NOT NULL,
    description TEXT NOT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY one_task_per_decision (source_decision_id),
    CONSTRAINT fk_task_approval FOREIGN KEY (approval_id) REFERENCES decision_loop.approval_record (id),
    CONSTRAINT fk_task_order FOREIGN KEY (order_id) REFERENCES procurement.purchase_order (order_id),
    CONSTRAINT fk_task_event FOREIGN KEY (event_id) REFERENCES procurement.supply_disruption_event (event_id)
) ENGINE=InnoDB;

-- 每次实际执行尝试追加一行，应用账号不能改、不能删。
CREATE TABLE decision_loop.execution_attempt (
    id BIGINT NOT NULL AUTO_INCREMENT PRIMARY KEY,
    decision_id BIGINT NOT NULL,
    outcome VARCHAR(20) NOT NULL,
    reason TEXT NOT NULL,
    mismatches JSON NOT NULL,
    semantic_fingerprint JSON NOT NULL,
    code_commit VARCHAR(64) NOT NULL,
    task_id BIGINT NULL,
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    CONSTRAINT fk_attempt_decision FOREIGN KEY (decision_id) REFERENCES decision_loop.decision_record (id),
    CONSTRAINT fk_attempt_task FOREIGN KEY (task_id) REFERENCES procurement.supply_risk_review_task (id),
    CONSTRAINT valid_attempt_outcome CHECK (outcome IN ('SUCCEEDED','FAILED','BLOCKED'))
) ENGINE=InnoDB;

CREATE USER 'decision_manager'@'%' IDENTIFIED BY 'demo_manager_db_password';
GRANT SELECT ON decision_loop.decision_record TO 'decision_manager'@'%';
GRANT UPDATE (approval_status, execution_status, occupancy, invalidation_reason)
    ON decision_loop.decision_record TO 'decision_manager'@'%';
GRANT SELECT ON decision_loop.app_user TO 'decision_manager'@'%';
GRANT SELECT, INSERT ON decision_loop.approval_record TO 'decision_manager'@'%';
GRANT SELECT ON decision_loop.approval_record TO 'decision_agent'@'%';
GRANT SELECT, INSERT ON decision_loop.execution_attempt TO 'decision_manager'@'%';
GRANT SELECT ON procurement.purchase_order TO 'decision_manager'@'%';
GRANT SELECT ON procurement.supply_disruption_event TO 'decision_manager'@'%';
GRANT SELECT, INSERT ON procurement.supply_risk_review_task TO 'decision_manager'@'%';

-- 追溯只读账号，不对应新的业务角色，不能插入、更新或删除。
CREATE USER 'decision_reader'@'%' IDENTIFIED BY 'demo_reader_db_password';
GRANT SELECT ON decision_loop.decision_record TO 'decision_reader'@'%';
GRANT SELECT ON decision_loop.approval_record TO 'decision_reader'@'%';
GRANT SELECT ON decision_loop.execution_attempt TO 'decision_reader'@'%';
GRANT SELECT ON procurement.supply_risk_review_task TO 'decision_reader'@'%';
