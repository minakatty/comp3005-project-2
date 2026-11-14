SELECT e.name, d.dname, p.pname FROM Emp e JOIN Dept d ON e.did = d.did JOIN Proj p ON p.did = d.did WHERE e.age > 65 AND p.pid < 40;
