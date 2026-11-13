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
    created_at TIMESTAMP(6) NOT NULL DEFAULT CURRENT_TIMESTAMP(6),
    UNIQUE KEY one_occupied_combination (order_id,event_id,occupancy),
    CONSTRAINT valid_occupancy CHECK (occupancy IS NULL OR occupancy=1)
) ENGINE=InnoDB;

CREATE USER 'decision_agent'@'%' IDENTIFIED BY 'demo_agent_db_password';
GRANT SELECT ON decision_loop.decision_record TO 'decision_agent'@'%';
GRANT INSERT (order_id,event_id,evidence,agent_note,description)
    ON decision_loop.decision_record TO 'decision_agent'@'%';
