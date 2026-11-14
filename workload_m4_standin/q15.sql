SELECT e.name, o.amount, d.dname FROM Emp e, Orders o, Dept d WHERE e.eid = o.eid AND e.did = d.did AND d.budget > 45000;
