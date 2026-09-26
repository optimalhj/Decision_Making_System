import gurobipy as gp
from gurobipy import GRB

def main():

    distances = {}
    with open("2.TSP_Distances.txt", "r", encoding="utf-8") as f:
        for idx1, line in enumerate(f):
            distances[idx1] = {idx2: dist for idx2, dist in enumerate([int(num) for num in line.strip().split(" ") if num != ""]) if idx1 != idx2}

    md = gp.Model()
    tsp = {}
    for start_idx in distances.keys():
        tsp[start_idx] = {end_idx: md.addVar(vtype=GRB.BINARY, name=f"{start_idx}->{end_idx}") for end_idx in distances[start_idx].keys()}
        md.addConstr(gp.quicksum(tsp[start_idx].values()) == 1)
    for end_idx in distances.keys(): md.addConstr(gp.quicksum(tsp[start_idx][end_idx] for start_idx in [start_idx for start_idx in distances.keys() if end_idx in distances[start_idx]]) == 1)
    total_distance = md.addVar(vtype=GRB.INTEGER, lb=0, name="total_distance")
    md.addConstr(total_distance == gp.quicksum(tsp[start_idx][end_idx] * distances[start_idx][end_idx] for start_idx in distances.keys() for end_idx in distances[start_idx].keys()))
    md.setObjective(total_distance, GRB.MINIMIZE)
    md.optimize()
    for _ in range(10):
        print()
    print(md.ObjVal)

    for start_idx in distances.keys():
        print(f"{start_idx :2d} : ", end="")
        for end_idx in distances[start_idx].keys():
            print(f"{end_idx:2d}/{round(tsp[start_idx][end_idx].X)}", end="   ")
        print()

if __name__ == "__main__":
    main()