SELECT e.name FROM Emp e JOIN Emp m ON e.mgr = m.eid WHERE m.age > 65;
