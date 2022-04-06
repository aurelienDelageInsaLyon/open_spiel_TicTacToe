# open_spiel_TicTacToe

## installation d'open_spiel

suivre le guide d'installation dans open_spiel/readme.md

## compilation

dans le répertoire open_spiel,
```
cd build; make -j$(nproc); cd ..
```

## entraîner alpha_zero

dans le répertoire open_spiel,

```
./build/examples/alpha_zero_torch_example --game=tic_tac_toe --path=. --nn_depth=2 --nn_width=8 --checkpoint_freq=1
```

## jouer 

dans le répertoire open_spiel,

```
./build/examples/alpha_zero_torch_game_example --game=tic_tac_toe --player1=az --player2=human --az_path=. --az_checkpoint=0
```

où l'argument player2=[...] permet de choisir entre nous (human), mcts ou az (alpha_zero)
