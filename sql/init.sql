SET NAMES utf8mb4;
USE procurement;

CREATE TABLE supplier (
    supplier_id INT NOT NULL PRIMARY KEY,
    supplier_name VARCHAR(100) NOT NULL,
    supplier_status VARCHAR(20) NOT NULL
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE purchase_order (
    order_id INT NOT NULL PRIMARY KEY,
    supplier_id INT NOT NULL,
    amount DECIMAL(12, 2) NOT NULL,
    order_status VARCHAR(20) NOT NULL,
    CONSTRAINT fk_order_supplier FOREIGN KEY (supplier_id)
        REFERENCES supplier (supplier_id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;

CREATE TABLE supply_disruption_event (
    event_id VARCHAR(20) NOT NULL PRIMARY KEY,
    affected_supplier_id INT NOT NULL,
    event_type VARCHAR(100) NOT NULL,
    is_current BOOLEAN NOT NULL,
    CONSTRAINT fk_event_supplier FOREIGN KEY (affected_supplier_id)
        REFERENCES supplier (supplier_id)
) CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_bin;

INSERT INTO supplier (supplier_id, supplier_name, supplier_status) VALUES
    (1, '华北钢材', 'DISABLED'),
    (2, '东方电子', 'ACTIVE');

INSERT INTO purchase_order (order_id, supplier_id, amount, order_status) VALUES
    (101, 1, 50000, 'PENDING'),
    (102, 1, 30000, 'COMPLETED'),
    (103, 2, 20000, 'PENDING');

INSERT INTO supply_disruption_event
    (event_id, affected_supplier_id, event_type, is_current)
VALUES ('E01', 1, 'DeliverySuspensionEvent', TRUE);

-- The endpoint needs metadata and SELECT access, not business-data writes.
REVOKE ALL PRIVILEGES ON procurement.* FROM 'ontop'@'%';
GRANT SELECT ON procurement.* TO 'ontop'@'%';
