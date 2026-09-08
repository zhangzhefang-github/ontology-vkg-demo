SELECT po.order_id, s.supplier_name, po.amount
FROM purchase_order AS po
JOIN supplier AS s ON po.supplier_id = s.supplier_id
WHERE s.supplier_status = 'DISABLED'
  AND po.order_status = 'PENDING'
ORDER BY po.order_id;
