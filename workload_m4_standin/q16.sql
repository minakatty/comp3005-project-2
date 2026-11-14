SELECT o.oid, p.pname FROM Orders o JOIN Emp e ON o.eid = e.eid JOIN Dept d ON e.did = d.did JOIN Proj p ON p.did = d.did WHERE o.amount > 990 AND p.pid < 20;
