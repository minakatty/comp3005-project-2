SELECT e.name, e.pad, o.amount FROM Emp e JOIN Orders o ON e.eid = o.eid WHERE e.city = 'c3' AND e.state = 's3' AND e.region = 'r3';
