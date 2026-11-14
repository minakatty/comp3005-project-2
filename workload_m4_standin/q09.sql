SELECT e.name, o.amount FROM Emp e JOIN Orders o ON e.eid = o.eid WHERE o.amount > 900;
