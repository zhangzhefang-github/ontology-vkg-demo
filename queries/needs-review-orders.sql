SELECT po.order_id, s.supplier_name, e.event_id
FROM purchase_order AS po
JOIN supplier AS s ON s.supplier_id = po.supplier_id
JOIN supply_disruption_event AS e
  ON e.affected_supplier_id = po.supplier_id
WHERE po.order_status = 'PENDING'
  AND e.is_current = TRUE
  AND e.event_type = 'DeliverySuspensionEvent'
ORDER BY po.order_id, e.event_id;
