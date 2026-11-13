REVOKE ALL PRIVILEGES ON procurement.* FROM 'ontop'@'%';
GRANT SELECT ON procurement.supplier TO 'ontop'@'%';
GRANT SELECT ON procurement.purchase_order TO 'ontop'@'%';
GRANT SELECT ON procurement.supply_disruption_event TO 'ontop'@'%';
