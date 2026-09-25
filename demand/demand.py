import torch
from torch import nn
from torch.nn import functional as F
from torch import optim

import numpy as np
import pandas as pd
from pathlib import Path

class Qnet(nn.Module):
    def __init__(self):
        super(Qnet, self).__init__()
        self.layer1 = nn.Linear(4, 128)
        self.layer2 = nn.Linear(128, 128)
        self.layer3 = nn.Linear(128, 64)
        self.layer4 = nn.Linear(64, 32)
        self.layer5 = nn.Linear(32, 16)
        self.layer6 = nn.Linear(16, 8)
        self.layer7 = nn.Linear(8, 1)
    def forward(self, x):
        x = self.layer1(x)
        x = F.relu(x)
        x = self.layer2(x)
        x = F.relu(x)
        x = self.layer3(x)
        x = F.relu(x)
        x = self.layer4(x)
        x = F.relu(x)
        x = self.layer5(x)
        x = F.relu(x)
        x = self.layer6(x)
        x = F.relu(x)
        x = self.layer7(x)
        return x

def build_list_state(demand_qty, rates, t, scaling):
    now_demand = demand_qty[t + 1]
    prior_rate = rates[t]

    rate_until_now = np.array(rates[:t + 1])
    mean_rate = np.mean(rate_until_now)
    std_rate = np.std(rate_until_now, ddof=1) if len(rate_until_now) > 2 else 0
    list_state = [now_demand / scaling, prior_rate, mean_rate, std_rate]
    return list_state

def train(demand_qty, rates, scaling, device, every_episode=5000, cycle=4, lr=0.001, batch_size=1):
    qnet = Qnet().to(device)
    optimizer = optim.Adam(qnet.parameters(), lr=lr)

    list_states, real_rates = [], []

    for t in range(len(demand_qty) - 1):
        list_state, real_rate = build_list_state(demand_qty, rates, t, scaling), [rates[t + 1]]
        list_states.append(list_state)
        real_rates.append(real_rate)

    for epoch in range(every_episode):
        selected_indices = np.random.choice(len(list_states), size=batch_size, replace=False)
        selected_list_states = [list_states[i] for i in selected_indices]
        selected_real_rates = [real_rates[i] for i in selected_indices]
        
        if (epoch + 1) % cycle == 0:
            tensor_states = torch.Tensor(selected_list_states).to(device)
            tensor_real_rates = torch.Tensor(selected_real_rates).to(device)

            pred_rate = qnet(tensor_states)
            loss = F.mse_loss(pred_rate, tensor_real_rates)
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

    return qnet.state_dict()

def test(demand_qty, rates, scaling, parameters, device):
    qnet = Qnet().to(device)
    qnet.load_state_dict(parameters)
    qnet.eval()

    list_state = build_list_state(demand_qty, rates, len(demand_qty) - 2, scaling)
    torch_state = torch.Tensor([list_state]).to(device)
    with torch.no_grad():
        pred_rate = qnet(torch_state)
    return pred_rate

def main():
    device = torch.device("cuda" if torch.cuda.is_available() else "mps" if torch.backends.mps.is_available() else "cpu")
    print("We are using", device, "\n")

    data_name = 'demand_data_p2'
    df = pd.read_excel(data_name+'.xls')
    demand_qty = df.DEMAND_QTY.values.tolist()
    demand_qty.reverse()

    scaling = 10000
    learning = False
    validating = False

    if learning or not Path(data_name+".pt").is_file():
        searching_indices = 400
        demand_qty = demand_qty[:min(searching_indices, len(demand_qty))]
        rates = [(demand_qty[t + 1] / demand_qty[t]) - 1 for t in range(len(demand_qty) - 1)]

        print("Today demand :", demand_qty.pop())

        print(len(demand_qty), demand_qty)
        print(len(rates), rates)

        torch.save(train(demand_qty, rates, scaling=scaling, device=device), data_name+'.pt')

    else:
        pred_demands, pred_rates = [], []

        if validating:
            validation_from_idx = 405
            demand_list = demand_qty[:min(validation_from_idx, len(demand_qty))]
            rates = [(demand_list[t + 1] / demand_list[t]) - 1 for t in range(len(demand_list) - 1)]
            print(demand_list[-1] == demand_qty[validation_from_idx - 1], demand_qty, "\n")

            for delta_day in range(5):
                print(len(demand_list + pred_demands), demand_list + pred_demands)
                print(len(rates + pred_rates), rates + pred_rates)
                print("Real Demand :" , demand_qty[validation_from_idx + delta_day], " /  Real Rate :", demand_qty[validation_from_idx + delta_day] / demand_qty[validation_from_idx + delta_day - 1] - 1)
                pred_rate = test(demand_list + pred_demands, rates + pred_rates, scaling, torch.load(data_name + ".pt"), device=device).item()
                pred_demand = ((demand_list + pred_demands)[-1] * (1 + pred_rate)) // 100 * 100
                print("Pred Demand :", pred_demand, " /  Pred Rate :", pred_rate)
                pred_demands.append(pred_demand)
                pred_rates.append(pred_rate)
                print()

        else:
            demand_list = demand_qty.copy()
            rates = [(demand_list[t + 1] / demand_list[t]) - 1 for t in range(len(demand_list) - 1)]

            for _ in range(7):
                print(len(demand_list), demand_list)
                print(len(rates), rates)
                pred_rate = test(demand_list + pred_demands, rates + pred_rates, scaling, torch.load(data_name + ".pt"), device=device).item()
                pred_demand = ((demand_list + pred_demands)[-1] * (1 + pred_rate)) // 100 * 100
                print("Pred Demand :", pred_demand, " /  Pred Rate :", pred_rate)
                pred_demands.append(pred_demand)
                pred_rates.append(pred_rate)
                print()
            print(pred_demands)

if __name__ == "__main__":
    main()