SELECT e.name, d.dname FROM Emp e JOIN Dept d ON e.did = d.did WHERE d.budget > 40000;
