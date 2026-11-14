SELECT e.name, d.title
  FROM Emp e JOIN Dept d ON e.did = d.did
 WHERE d.budget > 1000;
