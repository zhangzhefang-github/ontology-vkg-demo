USE procurement;

CREATE TABLE IF NOT EXISTS supply_disruption_event (
    event_id VARCHAR(20) NOT NULL PRIMARY KEY,
    affected_supplier_id INT NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    is_current BOOLEAN NOT NULL,
    CONSTRAINT fk_event_supplier FOREIGN KEY (affected_supplier_id)
        REFERENCES supplier (supplier_id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;

-- Idempotent upgrade: create E01 if absent. Existing current-state changes are
-- intentionally preserved; test cleanup is responsible for restoring TRUE.
INSERT INTO supply_disruption_event
    (event_id, affected_supplier_id, event_type, is_current)
VALUES ('E01', 1, 'DeliverySuspensionEvent', TRUE)
ON DUPLICATE KEY UPDATE
    affected_supplier_id = VALUES(affected_supplier_id),
    event_type = VALUES(event_type);
