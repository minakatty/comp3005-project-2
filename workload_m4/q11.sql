SELECT d.dname, COUNT(*) AS n FROM Emp e JOIN Dept d ON e.did = d.did GROUP BY d.dname;
