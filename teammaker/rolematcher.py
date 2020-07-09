import random
from itertools import combinations, permutations
from math import log, exp

from .base import Matcher


# custom mathematical functions
def sech2(x):
    return 4 * exp(-2 * x) / (exp(-2 * x) + 1) ** 2


class RoleMatcher(Matcher):

    NAME = 'role'

    @staticmethod
    def get_query(room_info, response_id):
        other_players = room_info['players'].copy()
        other_players.remove(room_info['response_ids'][response_id])
        return ('Roughly how many of the other players can you '
                'beat at each role?',
                'Answer as a value from 0 to 9. Other players are:',
                [
                    ('top', 'Top lane', 'number'),
                    ('jg', 'Jungle', 'number'),
                    ('mid', 'Mid lane', 'number'),
                    ('adc', 'AD Carry', 'number'),
                    ('sup', 'Support', 'number')
                ],
                'respond_page_rm1.html',
                other_players)

    @staticmethod
    def read_response(response):
        roles = ['top', 'jg', 'mid', 'adc', 'sup']
        values = []
        for role in roles:
            try:
                values.append(min(max(int(response.get(role, 0)), 0), 9))
            except (KeyError, ValueError):
                values.append(0)
        return values

    def __init__(self, epsilon=1e-5, strategy='fair'):
        super().__init__()
        self.epsilon = epsilon
        self.strategy = strategy

    def generate_teams(self, prefs):
        strategy_fn = {
            'fair': self.fair_logsum_strategy
        }[self.strategy]
        # we start with a random assortment of the two teams
        team1 = random.sample([i for i in range(10)], k=5)
        team2 = list({i for i in range(10)} - set(team1))
        random.shuffle(team2)
        # then we make changes to the teams and see
        # if some changes result in improvements to the teams
        happiness_1 = [prefs[p][i] for i, p in enumerate(team1)]
        happiness_2 = [prefs[p][i] for i, p in enumerate(team2)]
        best_score = strategy_fn(happiness_1, happiness_2)
        best_teams = (team1, team2)
        suggestions = self.dfs_greedy_search(team1, team2,
            best_score, strategy_fn, prefs, max_search=50
        )
        for suggest_t1, suggest_t2, score in suggestions:
            if score <= best_score:
                continue
            best_score = score
            best_teams = (suggest_t1, suggest_t2)
        bonus_info = []
        strategy_fn(
            [prefs[p][i] for i, p in enumerate(best_teams[0])],
            [prefs[p][i] for i, p in enumerate(best_teams[0])],
            print_fn=bonus_info.append,
            verbose=True
        )
        return best_teams, bonus_info[0]

    def team_transpose(self, t1, t2, move):
        t_new = t1 + t2
        t_new[move[0]], t_new[move[1]] = t_new[move[1]], t_new[move[0]]
        return t_new[:5], t_new[5:]

    def dfs_greedy_search(self, t1, t2, current_score, strategy_fn, prefs,
                          searched=None, max_search=5000, moves=None):
        searched = searched or [0]
        searched[0] += 1
        if searched[0] > max_search:
            return
        moves = moves or list(combinations([i for i in range(10)], 2))
        move_scores = []
        for move in moves:
            t1_, t2_ = self.team_transpose(t1, t2, move)
            h1 = [prefs[p][i] for i, p in enumerate(t1_)]
            h2 = [prefs[p][i] for i, p in enumerate(t2_)]
            move_scores.append((move, strategy_fn(h1, h2)))
        move_scores.sort(key=lambda x: x[1], reverse=True)
        for move, new_score in move_scores:
            if new_score < current_score:
                break
            t1_new, t2_new = self.team_transpose(t1, t2, move)
            yield from self.dfs_greedy_search(t1_new, t2_new, new_score,
                                              strategy_fn, prefs,
                                              searched=searched,
                                              max_search=max_search,
                                              moves=moves)
        yield t1, t2, current_score

    def fair_logsum_strategy(self, happiness_1, happiness_2, print_fn=None,
                             verbose=False):
        print_fn = print_fn or (print if verbose else None)
        # adjust for team fairness
        t1_score = sum(log(max(h, self.epsilon)) for h in happiness_1)
        t2_score = sum(log(max(h, self.epsilon)) for h in happiness_2)
        fairness_bonus = sech2(t1_score - t2_score) * 5
        # adjust for role fairness
        # system is penalised for making a difference in mirroring roles
        diff_softness = 2
        diff_penalty = sum((log(a + diff_softness) -
                            log(b + diff_softness)) ** 2 * 5
                           for a, b in zip(happiness_1, happiness_2))
        if print_fn is not None:
            diffs = [(log(a + diff_softness) -
                      log(b + diff_softness)) ** 2 * 5
                     for a, b in zip(happiness_1, happiness_2)]
            positives = t1_score + t2_score + fairness_bonus
            print_fn(f'Evaluation metrics:\n=======\nBonuses\n=======\n'
                     f'Team 1 happiness  {happiness_1}\n'
                     f'Team 2 happiness  {happiness_2}\n'
                     f'Team 1 score        {t1_score}\n'
                     f'Team 2 score        {t2_score}\n'
                     f'Fairness bonus    + {fairness_bonus}\n'
                     f'Calculation       = {positives}\n\n'
                     f'=========\nPenalties\n=========\n'
                     f'Differences      {diffs}\n'
                     f'Calculation      {diff_penalty}\n\n'
                     f'=====\nScore\n=====\n{positives - diff_penalty}\n')
        # + random.random() / 100
        return (t1_score + t2_score + fairness_bonus - diff_penalty)
