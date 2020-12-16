# @Time   : 2020/12/4
# @Author : Chenzhan Shang
# @Email  : czshang@outlook.com

# UPDATE
# @Time    :   2020/12/16
# @Author  :   Xiaolei Wang
# @email   :   wxl1999@foxmail.com

import torch
from loguru import logger

from crslab.evaluator.metrics.base_metrics import AverageMetric
from crslab.evaluator.metrics.gen_metrics import PPLMetric
from crslab.system.base_system import BaseSystem
from crslab.system.utils import ind2txt


class ReDialSystem(BaseSystem):
    def __init__(self, opt, train_dataloader, valid_dataloader, test_dataloader, vocab, side_data, restore=False,
                 debug=False):
        super(ReDialSystem, self).__init__(opt, train_dataloader, valid_dataloader, test_dataloader, vocab, side_data,
                                           restore, debug)
        self.ind2tok = vocab['conv']['ind2tok']
        self.end_token_idx = vocab['conv']['end']

        self.rec_optim_opt = opt['rec']
        self.conv_optim_opt = opt['conv']
        self.rec_epoch = self.rec_optim_opt['epoch']
        self.conv_epoch = self.conv_optim_opt['epoch']
        self.rec_batch_size = self.rec_optim_opt['batch_size']
        self.conv_batch_size = self.conv_optim_opt['batch_size']

    def rec_evaluate(self, rec_predict, movie_label):
        rec_predict = rec_predict.cpu().detach()
        _, rec_ranks = torch.topk(rec_predict, 50, dim=-1)
        movie_label = movie_label.cpu().detach()
        for rec_rank, movie in zip(rec_ranks, movie_label):
            self.evaluator.rec_evaluate(rec_rank, movie)

    def conv_evaluate(self, prediction, response):
        prediction = prediction.cpu().detach()
        response = response.cpu().detach()
        for p, r in zip(prediction, response):
            p_str = ind2txt(p, self.ind2tok, self.end_token_idx)
            r_str = ind2txt(r, self.ind2tok, self.end_token_idx)
            self.evaluator.gen_evaluate(p_str, [r_str])

    def step(self, batch, stage, mode):
        assert stage in ('rec', 'conv')
        assert mode in ('train', 'valid', 'test')

        for k, v in batch.items():
            if isinstance(v, torch.Tensor):
                batch[k] = v.to(self.device)

        if stage == 'rec':
            rec_loss, rec_scores = self.rec_model(batch, mode=mode)
            if mode == 'train':
                self.backward(rec_loss)
            else:
                self.rec_evaluate(rec_scores, batch['item'])
            rec_loss = rec_loss.item()
            self.evaluator.optim_metrics.add("rec_loss", AverageMetric(rec_loss))
        else:
            gen_loss, preds = self.conv_model(batch, mode=mode)
            if mode == 'train':
                self.backward(gen_loss)
            else:
                self.conv_evaluate(preds, batch['response'])
            gen_loss = gen_loss.item()
            self.evaluator.optim_metrics.add('gen_loss', AverageMetric(gen_loss))
            self.evaluator.gen_metrics.add('ppl', PPLMetric(gen_loss))

    def train_recommender(self):
        self.init_optim(self.rec_optim_opt, self.rec_model.parameters())

        for epoch in range(self.rec_epoch):
            self.evaluator.reset_metrics()
            logger.info(f'[Recommendation epoch {str(epoch)}]')
            logger.info('[Train]')
            for batch in self.train_dataloader['rec'].get_rec_data(batch_size=self.rec_batch_size):
                self.step(batch, stage='rec', mode='train')
            self.evaluator.report()  # report train loss
            # val
            logger.info('[Valid]')
            with torch.no_grad():
                self.evaluator.reset_metrics()
                for batch in self.valid_dataloader['rec'].get_rec_data(batch_size=self.rec_batch_size, shuffle=False):
                    self.step(batch, stage='rec', mode='valid')
                self.evaluator.report()  # report valid loss
                # early stop
                metric = self.evaluator.optim_metrics['rec_loss']
                if self.early_stop(metric):
                    break
        # test
        logger.info('[Test]')
        with torch.no_grad():
            self.evaluator.reset_metrics()
            for batch in self.test_dataloader['rec'].get_rec_data(batch_size=self.rec_batch_size, shuffle=False):
                self.step(batch, stage='rec', mode='test')
            self.evaluator.report()

    def train_conversation(self):
        self.init_optim(self.conv_optim_opt, self.conv_model.parameters())

        for epoch in range(self.conv_epoch):
            self.evaluator.reset_metrics()
            logger.info(f'[Conversation epoch {str(epoch)}]')
            logger.info('[Train]')
            for batch in self.train_dataloader['conv'].get_conv_data(batch_size=self.conv_batch_size):
                self.step(batch, stage='conv', mode='train')
            self.evaluator.report()
            # val
            logger.info('[Valid]')
            with torch.no_grad():
                self.evaluator.reset_metrics()
                for batch in self.valid_dataloader['conv'].get_conv_data(batch_size=self.conv_batch_size,
                                                                         shuffle=False):
                    self.step(batch, stage='conv', mode='valid')
                self.evaluator.report()
                metric = self.evaluator.optim_metrics['gen_loss']
                if self.early_stop(metric):
                    break
        # test
        logger.info('[Test]')
        with torch.no_grad():
            self.evaluator.reset_metrics()
            for batch in self.test_dataloader['conv'].get_conv_data(batch_size=self.conv_batch_size, shuffle=False):
                self.step(batch, stage='conv', mode='test')
            self.evaluator.report()

    def fit(self):
        self.train_recommender()
        self.train_conversation()