SELECT p.pname, d.dname FROM Proj p JOIN Dept d ON p.did = d.did WHERE p.pid < 50;
